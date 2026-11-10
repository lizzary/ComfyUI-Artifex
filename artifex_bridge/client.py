"""Small synchronous client for the existing, transactional Artifex HTTP API."""

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import re
import secrets
import threading
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from .process import ArtifexUnavailable

log = logging.getLogger("comfyui.artifex")
DEFAULT_GROUP = "ComfyUI"
# Artifex frontend/src/hooks/useGroupConfig.js: GROUP_COLORS.
GROUP_COLORS = (
    (239, 68, 68), (59, 130, 246), (34, 197, 94), (251, 191, 36),
    (168, 85, 247), (236, 72, 153), (20, 184, 166), (249, 115, 22),
    (99, 102, 241), (244, 114, 182),
)


class ArtifexError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class ArtifexTaggingError(ArtifexError):
    """The original is already durable; do not replay its upload on failure."""

    def __init__(self, illustration, reason, status=None):
        super().__init__(
            f'Image {illustration["id"]} was saved in Artifex, but tagging failed: {reason}',
            status,
        )
        self.illustration = illustration


@dataclass(frozen=True)
class UploadOutcome:
    status: str
    illustration: dict | None = None
    reason: str = ""


def _decorate(items):
    counts = Counter(item["name"] for item in items)
    scoped_ids = Counter((item["name"], item["id"]) for item in items)
    decorated = []
    for item in items:
        identifier = item["value"] if scoped_ids[(item["name"], item["id"])] > 1 else item["id"]
        label = f'{item["name"]}(id:{identifier})' if counts[item["name"]] > 1 else item["name"]
        decorated.append(dict(item, label=label))
    return decorated


def _input_name(value):
    # Some ComfyUI text nodes (e.g. TruncateText) wrap a scalar STRING in a
    # one-item list without declaring a list output. Accept that wrapper,
    # but keep ambiguous lists and non-text inputs on the normal fallback path.
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    return value.strip() if isinstance(value, str) else ""


def _random_group_colors():
    # Match Artifex's groupColorsFromHex opacity without consuming the random
    # state used by ComfyUI workflows. Pick once, when the group is created.
    red, green, blue = secrets.choice(GROUP_COLORS)
    return {
        "color": f"rgba({red}, {green}, {blue}, 0.08)",
        "borderColor": f"rgba({red}, {green}, {blue}, 0.35)",
    }


def _pair_name(pair):
    if pair.get("customName", "").strip():
        return pair["customName"].strip()
    chunks = []
    for index, term in enumerate(pair.get("terms", [])):
        scope = term.get("scope", "all")
        chunks.append(
            (f' {term.get("operator", "and").upper()} ' if index else "")
            + "(" * term.get("open", 0) + ("NOT " if term.get("negated") else "")
            + (scope + ":" if scope in {"tag", "prompt"} else "")
            + term.get("value", "") + ")" * term.get("close", 0)
        )
    return "".join(chunks)


def _matches(image, pair):
    """Mirror Artifex grouping.js: AND precedence, parentheses, NOT, exact tags."""
    terms = pair.get("terms", [])
    if not terms or any(not term.get("value", "").strip() for term in terms):
        return False
    tags = {tag.strip().lower() for tag in image.get("tags", "").split(",") if tag.strip()}
    ext = image.get("extended_data") or {}
    prompt = " ".join(str(ext.get(key, "")) for key in ("Positive Prompt", "Negative Prompt")).lower()
    values, operators = [], []
    precedence = {"or": 1, "and": 2}

    def reduce():
        right, left = values.pop(), values.pop()
        values.append(left and right if operators.pop() == "and" else left or right)

    try:
        for index, term in enumerate(terms):
            operator = term.get("operator", "and")
            if index:
                while operators and operators[-1] != "(" and precedence[operators[-1]] >= precedence[operator]:
                    reduce()
                operators.append(operator)
            operators.extend(["("] * term.get("open", 0))
            keyword = term["value"].strip().lower()
            scope = term.get("scope", "all")
            match = keyword in tags if scope == "tag" else keyword in prompt if scope == "prompt" else keyword in tags or keyword in prompt
            values.append(not match if term.get("negated") else match)
            for _ in range(term.get("close", 0)):
                while operators and operators[-1] != "(":
                    reduce()
                if not operators:
                    return False
                operators.pop()
        while operators:
            if operators[-1] == "(":
                return False
            reduce()
        return len(values) == 1 and values[0]
    except (IndexError, KeyError, TypeError):
        return False


class ArtifexClient:
    def __init__(self, process):
        self.process = process
        self._opener = build_opener(ProxyHandler({}))
        self._resolve_lock = threading.RLock()
        self._tagging_lock = threading.Lock()
        self._context = threading.local()

    @contextmanager
    def library_session(self):
        """Pin a multi-request operation to one library, including name resolution."""
        self.process.ensure_running()
        previous = getattr(self._context, "library_id", None)
        library_id = previous or self.process.library_id
        self._context.library_id = library_id
        try:
            yield library_id
        finally:
            self._context.library_id = previous

    def _request(self, method, path, *, payload=None, body=None, content_type=None, library_id=None, raw=False):
        base_url = self.process.ensure_running()
        headers = {"Accept": "application/json"}
        library_id = library_id or getattr(self._context, "library_id", None)
        expected = self.process.expected_library(library_id) if library_id else self.process.library_id
        if expected:
            headers["X-Artifex-Library"] = expected
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        try:
            with self._opener.open(Request(base_url + path, data=body, headers=headers, method=method), timeout=self.process.config.request_timeout) as response:
                data = response.read()
                if raw:
                    return data, response.headers.get("Content-Type", "application/octet-stream")
                return json.loads(data) if data else None
        except HTTPError as exc:
            data = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(data).get("detail", data)
            except (ValueError, AttributeError):
                detail = data
            raise ArtifexError(f"Artifex HTTP {exc.code}: {detail}", exc.code) from exc
        except (URLError, OSError, ValueError) as exc:
            # A timed-out upload may already have committed: do not blindly replay it.
            raise ArtifexError(f"Artifex request failed ({method} {path}): {exc}") from exc

    def groups(self):
        return _decorate([dict(group, value=f'id:{group["id"]}') for group in self._request("GET", "/api/groups")])

    def resolve_group(self, name=None, selected="default"):
        with self._resolve_lock:
            clean = _input_name(name)
            if clean:
                try:
                    # Artifex checks Go len(string), i.e. UTF-8 bytes.
                    if len(clean.encode("utf-8")) > 100:
                        raise ValueError("Group names must fit in 100 UTF-8 bytes")
                    matches = [group for group in self.groups() if group["name"] == clean]
                    if matches:
                        return max(matches, key=lambda group: (group["illustration_count"], -group["id"]))
                    return self._request("POST", "/api/groups", payload={"name": clean})
                except (ArtifexError, ValueError) as exc:
                    log.warning("Group input could not be resolved; using the selected group: %s", exc)
            groups = self.groups()
            for group in groups:
                if str(selected) in {group["value"], group["label"], str(group["id"])}:
                    return group
            matches = [group for group in groups if group["name"] == DEFAULT_GROUP]
            if matches:
                return max(matches, key=lambda group: (group["illustration_count"], -group["id"]))
            return self._request("POST", "/api/groups", payload={"name": DEFAULT_GROUP})

    def _color_config(self, group_id):
        config = self._request("GET", "/api/group-config")
        return config.get("groups", {}).get(str(group_id), {}).get("colors", {"sets": [], "active_id": None})

    def illustrations(self, group_id):
        items = []
        while True:
            page = self._request("GET", f"/api/groups/{int(group_id)}/illustrations?offset={len(items)}&limit=5000")
            items.extend(page["items"])
            if len(items) >= page["total"] or not page["items"]:
                return items
            if len(items) > 100000:
                raise ArtifexError("Artifex pagination cannot reliably count color groups beyond 100000 images")

    def color_groups(self, group_id):
        colors = self._color_config(group_id)
        pairs = []
        # Color groups belong to a board/set. Include every board so saved choices
        # remain meaningful when the user changes the currently active board.
        for color_set in colors.get("sets", []):
            for pair in color_set.get("pairs", []):
                pairs.append(dict(pair, set_id=color_set["id"], name=_pair_name(pair),
                                  value=f'{color_set["id"]}:{pair["id"]}'))
        return _decorate(pairs)

    def _color_counts(self, group_id, colors):
        counts = Counter()
        images = self.illustrations(group_id)
        for color_set in colors.get("sets", []):
            pairs = {pair["id"]: pair for pair in color_set.get("pairs", [])}
            order = list(dict.fromkeys(color_set.get("match_order", []) + list(pairs)))
            assignments = color_set.get("manual_assignments", {})
            for image in images:
                target = assignments.get(str(image["id"]))
                if target == "":
                    # Artifex uses an empty assignment to explicitly leave an image ungrouped.
                    continue
                if target not in pairs:
                    target = next((pair_id for pair_id in order if pair_id in pairs and _matches(image, pairs[pair_id])), None)
                if target:
                    counts[(color_set["id"], target)] += 1
        return counts

    def resolve_color(self, group_id, name=None, selected=""):
        with self._resolve_lock:
            clean = _input_name(name)
            if clean:
                try:
                    colors = self._color_config(group_id)
                    matches = [(color_set, pair) for color_set in colors.get("sets", [])
                               for pair in color_set.get("pairs", []) if _pair_name(pair) == clean]
                    if matches:
                        counts = self._color_counts(group_id, colors) if len(matches) > 1 else Counter()
                        color_set, pair = max(matches, key=lambda item: (counts[(item[0]["id"], item[1]["id"])], item[0]["id"], item[1]["id"]))
                        return dict(pair, set_id=color_set["id"], name=clean, value=f'{color_set["id"]}:{pair["id"]}')
                    set_id = colors.get("active_id")
                    operations = []
                    if not set_id:
                        set_id = "artifex-" + uuid4().hex
                        operations.append({"type": "create_set", "set": {"id": set_id, "name": "ComfyUI"}})
                    pair = {"id": uuid4().hex, "customName": clean, "terms": [], **_random_group_colors()}
                    operations.append({"type": "create_pair", "set_id": set_id, "pair": pair})
                    self._request("POST", f"/api/groups/{int(group_id)}/color-operations", payload={"operations": operations})
                    return dict(pair, set_id=set_id, name=clean, value=f'{set_id}:{pair["id"]}')
                except (ArtifexError, ValueError) as exc:
                    log.warning("Color-group input could not be resolved; using the selected color group: %s", exc)
            if selected and selected not in {"none", "default"}:
                try:
                    for pair in self.color_groups(group_id):
                        if selected in {pair["value"], pair["label"]}:
                            return pair
                except ArtifexError as exc:
                    log.warning("Selected color group is unavailable; uploading without a color group: %s", exc)
            return None

    def auto_tag_available(self):
        # Capability validation is intentionally left as a permissive stub.
        # The existing tagging endpoint still reports model/runtime failures.
        return True

    def _ensure_tagging_enabled(self, library_id=None):
        # This existing settings endpoint loads an installed model, without
        # downloading anything. Keep it enabled: restoring false unloads the
        # engine and would race other requests. Nodes opt out per upload.
        with self._tagging_lock:
            settings = self._request("GET", "/api/settings", library_id=library_id)
            if not settings.get("auto_tag", False):
                self._request("PUT", "/api/settings", payload={"auto_tag": True}, library_id=library_id)

    def _tag_image(self, illustration, library_id=None):
        try:
            result = self._request("POST", "/api/illustrations/retag",
                                   payload={"ids": [illustration["id"]]}, library_id=library_id)
            updated = next((item for item in result.get("updated", []) if item["id"] == illustration["id"]), None)
            if updated is not None:
                return updated
            failures = result.get("failed", [])
            reason = failures[0].get("error", "Tagging failed") if failures else "Artifex returned no tagging outcome"
            raise ArtifexError(reason)
        except (ArtifexError, ArtifexUnavailable) as exc:
            raise ArtifexTaggingError(illustration, str(exc), getattr(exc, "status", None)) from exc

    def upload(self, group_id, filename, data, auto_tag=False, library_id=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise ValueError("Upload filenames must be generated safe basenames")
        tag_requested = bool(auto_tag and self.auto_tag_available())
        if tag_requested:
            self._ensure_tagging_enabled(library_id=library_id)
        boundary = "Artifex" + uuid4().hex
        parts = []
        # Per-image retag is independent of Artifex's global upload toggle.
        # Never temporarily mutate global settings around concurrent uploads.
        for key, value in {"skip_auto_tag": "true", "conflict_policy": "save_all"}.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode(),
            data, f"\r\n--{boundary}--\r\n".encode(),
        ])
        result = self._request("POST", f"/api/groups/{int(group_id)}/illustrations/upload", body=b"".join(parts),
                               content_type=f"multipart/form-data; boundary={boundary}", library_id=library_id)
        if result.get("added"):
            illustration = result["added"][0]
            if tag_requested:
                illustration = self._tag_image(illustration, library_id=library_id)
            return UploadOutcome("saved", illustration)
        if result.get("skipped"):
            return UploadOutcome("skipped", reason="Artifex skipped the file")
        failures = result.get("failed", [])
        reason = failures[0].get("error", "Upload failed") if failures else "Artifex returned no upload outcome"
        if reason.startswith("cannot identify image:") or "exceeds the 100 megapixel image limit" in reason or "exceeds the 2 GiB upload limit" in reason:
            return UploadOutcome("skipped", reason=reason)
        raise ArtifexError(reason)

    def assign_color(self, group_id, color, illustration_id, library_id=None):
        if color is not None:
            self._request("POST", f"/api/groups/{int(group_id)}/color-operations", library_id=library_id,
                          payload={"operations": [{"type": "assign", "set_id": color["set_id"], "assignments": {str(illustration_id): color["id"]}}]})

    def delete_image(self, illustration_id, library_id=None):
        try:
            self._request("DELETE", f"/api/illustrations/{int(illustration_id)}", library_id=library_id)
        except ArtifexError as exc:
            if exc.status != 404:
                raise

    def image_bytes(self, illustration_id, library_id=None):
        return self._request("GET", f"/api/illustrations/{int(illustration_id)}/file", library_id=library_id, raw=True)

    def gallery_url(self, group_id=None):
        # Artifex has no group deep link. The plugin opens its unmodified home page.
        return self.process.ensure_running() + "/"
