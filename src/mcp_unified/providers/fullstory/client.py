"""Cliente da API da FullStory (v1 e v2).

Port do `Fullstory.js` do fs-lexicon. Duas particularidades vindas do original:

- O ID de sessão nos endpoints v2 é **composto**: `{user_id}:{session_id}`.
- A v1 e a v2 convivem: users e events estão na v2, mas segments, settings e
  os endpoints `individual/` continuam na v1.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote, urlencode

from ...config import FullStorySettings
from ...http import BaseApiClient

API_V2 = "v2"


class FullStoryClient(BaseApiClient):
    provider_name = "fullstory"

    def __init__(self, settings: FullStorySettings, **kwargs: Any) -> None:
        self.settings = settings
        super().__init__(settings.base_url, headers=self._auth_headers(settings), **kwargs)

    @staticmethod
    def _auth_headers(settings: FullStorySettings) -> dict[str, str]:
        token = settings.api_key or ""
        # O original aceita a chave já em Basic ou crua; normaliza aqui.
        if not token.startswith("Basic "):
            probe = token
            try:
                base64.b64decode(probe, validate=True)
            except Exception:
                probe = base64.b64encode(token.encode()).decode()
            token = probe
        else:
            token = token[len("Basic ") :]
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _forbidden_hint(self) -> str:
        return (
            "permissão insuficiente (403) — a chave da FullStory precisa dos escopos "
            "de leitura de sessão/usuário. Verifique em Settings > API Keys."
        )

    # ------------------------------------------------------------ utilitários

    @staticmethod
    def format_session_id(user_id: str, session_id: str) -> str:
        """Monta o ID composto usado pelos endpoints v2 de sessão."""
        if not user_id or not session_id:
            raise ValueError("user_id e session_id são obrigatórios")
        return f"{user_id}:{session_id}"

    def session_link(self, user_id: str, session_id: str) -> str:
        """URL do replay. É o caminho prático para inspeção visual, já que a
        API de screenshots não é pública."""
        org = self.settings.org_id or "org"
        return f"https://app.fullstory.com/ui/{org}/session/{user_id}:{session_id}"

    # -------------------------------------------------------- session profiles

    async def get_session_profile(self, profile_id: str) -> Any:
        return await self.get(f"{API_V2}/visit_profile/{quote(profile_id, safe='')}")

    async def list_session_profiles(self, **params: Any) -> Any:
        return await self.get(f"{API_V2}/visit_profile", params=params)

    async def create_session_profile(self, payload: dict[str, Any]) -> Any:
        return await self.post(f"{API_V2}/visit_profile", json=payload)

    async def update_session_profile(self, profile_id: str, payload: dict[str, Any]) -> Any:
        return await self.post(
            f"{API_V2}/visit_profile/{quote(profile_id, safe='')}", json=payload
        )

    async def delete_session_profile(self, profile_id: str) -> Any:
        return await self.delete(f"{API_V2}/visit_profile/{quote(profile_id, safe='')}")

    # ---------------------------------------------------------------- sessions

    async def get_session_events(
        self, user_id: str, session_id: str, *, enable_event_cache: bool | None = None
    ) -> Any:
        sid = self.format_session_id(user_id, session_id)
        params = (
            {"enable_event_cache": str(enable_event_cache).lower()}
            if enable_event_cache is not None
            else None
        )
        return await self.get(f"{API_V2}/sessions/{sid}/events", params=params)

    async def generate_session_context(
        self, user_id: str, session_id: str, options: dict[str, Any] | None = None
    ) -> Any:
        sid = self.format_session_id(user_id, session_id)
        query = f"?{urlencode(options)}" if options else ""
        return await self.post(f"{API_V2}/sessions/{sid}/context{query}", json={})

    async def get_session_summary(
        self, user_id: str, session_id: str, config_profile: str | None = None
    ) -> Any:
        sid = self.format_session_id(user_id, session_id)
        params = {"config_profile": config_profile} if config_profile else None
        return await self.get(f"{API_V2}/sessions/{sid}/summary", params=params)

    async def list_sessions(
        self, *, uid: str | None = None, email: str | None = None, limit: int | None = None
    ) -> Any:
        if not uid and not email:
            raise ValueError("informe uid ou email para listar sessões")
        return await self.get(
            "v1/sessions", params={"uid": uid, "email": email, "limit": limit}
        )

    async def search_sessions(self, criteria: dict[str, Any]) -> Any:
        return await self.post(f"{API_V2}/sessions/search", json=criteria)

    # ------------------------------------------------------------- users (v2)

    async def create_user(self, payload: dict[str, Any]) -> Any:
        return await self.post(f"{API_V2}/users", json=payload)

    async def get_user(self, user_id: str) -> Any:
        return await self.get(f"{API_V2}/users/{quote(user_id, safe='')}")

    async def update_user(self, user_id: str, updates: dict[str, Any]) -> Any:
        return await self.post(f"{API_V2}/users/{quote(user_id, safe='')}", json=updates)

    async def delete_user(self, user_id: str) -> Any:
        return await self.delete(f"{API_V2}/users/{quote(user_id, safe='')}")

    async def create_users_batch(self, users: list[dict[str, Any]]) -> Any:
        return await self.post(f"{API_V2}/users/batch", json={"users": users})

    # ------------------------------------------------------------- users (v1)

    async def set_user_properties_v1(self, uid: str, properties: dict[str, Any]) -> Any:
        return await self.post(
            f"users/v1/individual/{quote(uid, safe='')}/customvars", json=properties
        )

    async def set_user_events_v1(self, uid: str, events: dict[str, Any]) -> Any:
        return await self.post(
            f"users/v1/individual/{quote(uid, safe='')}/customevent", json=events
        )

    async def get_user_events(self, uid: str, options: dict[str, Any] | None = None) -> Any:
        return await self.get(
            f"users/v1/individual/{quote(uid, safe='')}/events", params=options
        )

    async def get_user_pages(self, uid: str, options: dict[str, Any] | None = None) -> Any:
        return await self.get(
            f"users/v1/individual/{quote(uid, safe='')}/pages", params=options
        )

    # ------------------------------------------------------------------ events

    async def create_event(self, payload: dict[str, Any]) -> Any:
        return await self.post(f"{API_V2}/events", json=payload)

    async def create_events_batch(self, events: list[dict[str, Any]]) -> Any:
        return await self.post(f"{API_V2}/events/batch", json={"events": events})

    async def create_annotation(self, payload: dict[str, Any]) -> Any:
        return await self.post(f"{API_V2}/annotations", json=payload)

    # -------------------------------------------------------------- batch jobs

    async def get_batch_job_status(self, job_id: str) -> Any:
        return await self.get(f"{API_V2}/batch/{quote(job_id, safe='')}")

    async def get_batch_job_errors(self, job_id: str) -> Any:
        return await self.get(f"{API_V2}/batch/{quote(job_id, safe='')}/errors")

    # ---------------------------------------------------------------- segments

    async def create_segment_export(self, payload: dict[str, Any]) -> Any:
        return await self.post("segments/v1/exports", json=payload)

    async def get_segment_export_status(self, export_id: str) -> Any:
        return await self.get(f"segments/v1/exports/{quote(export_id, safe='')}")

    async def list_segments(self, **params: Any) -> Any:
        return await self.get("segments/v1", params=params)

    async def get_segment(self, segment_id: str) -> Any:
        return await self.get(f"segments/v1/{quote(segment_id, safe='')}")

    # ---------------------------------------------------------------- settings

    async def get_recording_block_rules(self) -> Any:
        return await self.get("settings/recording/v1/blocking")

    # ------------------------------------------------------------------ health

    async def health_check(self) -> dict[str, Any]:
        """Ping barato. Usa list_segments porque não exige argumentos e é leve."""
        try:
            await self.list_segments(limit=1)
        except Exception as exc:  # noqa: BLE001 — health check reporta, não propaga
            return {"status": "unhealthy", "provider": "fullstory", "detail": str(exc)}
        return {
            "status": "healthy",
            "provider": "fullstory",
            "datacenter": self.settings.datacenter,
            "base_url": self.base_url,
        }
