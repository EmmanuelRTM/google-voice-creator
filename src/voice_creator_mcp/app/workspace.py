"""Per-principal workspaces so one user's voice data is never visible to another.

Local (stdio or unauthenticated loopback) deployments share a single workspace.
Authenticated HTTP deployments give every principal (issuer + subject) a private
directory under `<data_dir>/principals/<opaque id>/` with owner-only permissions.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from voice_creator_mcp.app.backend import Backend
from voice_creator_mcp.app.dialogue_studio import DialogueStudio
from voice_creator_mcp.app.outputs import OutputWriter
from voice_creator_mcp.app.voice_library import VoiceLibrary
from voice_creator_mcp.auth.principal import Principal
from voice_creator_mcp.errors import AuthorizationError
from voice_creator_mcp.voices.profiles import ProfileStore

LOCAL_WORKSPACE_ID = "local"


@dataclass(frozen=True)
class Workspace:
    id: str
    library: VoiceLibrary
    studio: DialogueStudio
    output_dir: Path


class WorkspaceProvider:
    def __init__(
        self,
        *,
        data_dir: Path,
        output_dir: Path,
        backend: Backend,
        default_live_model: str,
        isolate: bool,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._output_dir = Path(output_dir)
        self._backend = backend
        self._default_live_model = default_live_model
        self._isolate = isolate
        self._clock = clock
        self._cache: dict[str, Workspace] = {}
        self._lock = threading.Lock()

    def get(self, principal: Principal | None) -> Workspace:
        if not self._isolate:
            return self._cached(LOCAL_WORKSPACE_ID, self._data_dir, self._output_dir)
        if principal is None:
            raise AuthorizationError("Authentication required")
        root = self._data_dir / "principals" / principal.workspace_id
        return self._cached(principal.workspace_id, root, root / "output")

    def _cached(self, workspace_id: str, root: Path, output_dir: Path) -> Workspace:
        with self._lock:
            if workspace_id not in self._cache:
                self._cache[workspace_id] = self._build(workspace_id, root, output_dir)
            return self._cache[workspace_id]

    def _build(self, workspace_id: str, root: Path, output_dir: Path) -> Workspace:
        if self._isolate:
            root.mkdir(parents=True, exist_ok=True)
            for directory in (root.parent, root):  # principals/ and the workspace itself
                directory.chmod(0o700)
        library = VoiceLibrary(ProfileStore(root), self._backend, clock=self._clock)
        studio = DialogueStudio(
            library,
            self._backend,
            OutputWriter(output_dir, clock=self._clock),
            default_live_model=self._default_live_model,
        )
        return Workspace(id=workspace_id, library=library, studio=studio, output_dir=output_dir)
