from __future__ import annotations

from dataclasses import dataclass, field

from omni.modules.units import UNITS


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    name: str
    kind: str  # "lora" | "tool-schema" | "prompt-pack"
    size_mb: float
    params_m: float
    covers: dict[str, float]
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.size_mb <= 0:
            raise ValueError(f"{self.id}: size_mb must be positive")
        if any(unit not in UNITS for unit in self.covers):
            unknown = sorted(set(self.covers) - set(UNITS))
            raise ValueError(f"{self.id}: unknown capability units {unknown}")
        if any(not 0.0 < float(p) <= 1.0 for p in self.covers.values()):
            raise ValueError(f"{self.id}: coverage probabilities must be in (0, 1]")


# Declared sizes and coverage are hand-authored design priors, not measured
# performance. Phase 4/5 should calibrate them against real runs.
DEFAULT_MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec("files-core", "Files Core", "tool-schema", 8.0, 0.0, {"file-edit": 0.90, "file-search": 0.60, "clipboard": 0.40}, "Filesystem actions and workspace-scoped path handling.", ("files", "paths")),
    ModuleSpec("editor-lora", "Editor Adapter", "lora", 24.0, 6.0, {"doc-edit": 0.85, "code-edit": 0.35, "clipboard": 0.20}, "Text/document editing adapter.", ("document", "editor")),
    ModuleSpec("terminal-tools", "Terminal Tools", "tool-schema", 14.0, 0.0, {"terminal": 0.90, "test-run": 0.70, "git": 0.55, "file-search": 0.30}, "Shell, build, test and git tool schemas.", ("terminal", "dev")),
    ModuleSpec("browser-tools", "Browser Tools", "tool-schema", 12.0, 0.0, {"browser-nav": 0.85, "forms": 0.60, "download": 0.55, "search": 0.35}, "Tab, URL, form and download controls.", ("browser", "web")),
    ModuleSpec("vision-lora", "Vision Adapter", "lora", 96.0, 24.0, {"vision": 0.90, "window-control": 0.45, "forms": 0.25}, "Visual grounding for screenshots and layout.", ("vision", "screen")),
    ModuleSpec("planner-pack", "Planner Pack", "prompt-pack", 10.0, 0.0, {"planning": 0.80, "cross-app": 0.70, "window-control": 0.20}, "Decomposition and cross-application handoff prompts.", ("planning", "workflow")),
    ModuleSpec("office-tools", "Office Tools", "tool-schema", 18.0, 0.0, {"doc-edit": 0.65, "data-table": 0.55, "forms": 0.45, "clipboard": 0.25}, "Document, spreadsheet and form operations.", ("office", "documents")),
    ModuleSpec("dev-tools", "Developer Adapter", "lora", 22.0, 5.0, {"code-edit": 0.80, "test-run": 0.55, "git": 0.70, "terminal": 0.30}, "Code editing and repository workflows.", ("code", "dev")),
    ModuleSpec("web-scrape", "Web Extract", "tool-schema", 10.0, 0.0, {"search": 0.80, "browser-nav": 0.45, "data-table": 0.35, "download": 0.25}, "Page text/table extraction and search.", ("web", "search")),
    ModuleSpec("clipboard-pack", "Clipboard Pack", "tool-schema", 4.0, 0.0, {"clipboard": 0.75, "file-edit": 0.30}, "Clipboard read/write without storing content.", ("clipboard",)),
    ModuleSpec("accessibility-pack", "Accessibility Pack", "tool-schema", 6.0, 0.0, {"window-control": 0.65, "forms": 0.50, "vision": 0.15}, "AT-SPI element actions and focus control.", ("atspi", "a11y")),
)


class ModuleLibrary:
    def __init__(self, modules: tuple[ModuleSpec, ...] = DEFAULT_MODULES) -> None:
        self.modules = tuple(modules)
        self.by_id = {m.id: m for m in self.modules}
        if len(self.by_id) != len(self.modules):
            raise ValueError("duplicate module id")

    def get(self, module_id: str) -> ModuleSpec:
        return self.by_id[module_id]

    def total_size_mb(self) -> float:
        return sum(m.size_mb for m in self.modules)
