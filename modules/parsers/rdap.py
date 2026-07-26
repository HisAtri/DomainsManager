import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

import idna

from modules.errors import ResponseParseError
from modules.models.domain import (
    DNSSECInfo,
    DomainDates,
    DomainInfo,
    NormalizedDomain,
    RegistrarInfo,
)
from modules.models.response import RawLookupResponse


class RdapParser:
    """把 RFC 9083 域名响应转换为应用层的统一域名模型。"""

    VERSION = "2"

    def parse(
        self,
        response: RawLookupResponse,
        domain: NormalizedDomain,
    ) -> DomainInfo:
        payload = self._load_payload(response)
        self._raise_for_error_response(payload, response.status_code)

        object_class = payload.get("objectClassName")
        if object_class is not None and (
            not isinstance(object_class, str)
            or object_class.strip().casefold() != "domain"
        ):
            raise ResponseParseError("RDAP 响应不是 domain 对象")

        response_domain = self._parse_domain_name(payload, domain)
        events = self._parse_events(self._optional_list(payload, "events"))
        entities = self._optional_list(payload, "entities")
        registrar = self._parse_registrar(entities)
        nameservers = self._parse_nameservers(
            self._optional_list(payload, "nameservers")
        )
        statuses = self._parse_statuses(self._optional_list(payload, "status"))
        secure_dns = self._optional_mapping(payload, "secureDNS")
        delegation_signed = secure_dns.get("delegationSigned")
        if not isinstance(delegation_signed, bool):
            delegation_signed = None

        handle = payload.get("handle")
        if handle is not None and not isinstance(handle, str):
            handle = str(handle)

        return DomainInfo(
            domain=response_domain,
            registry_handle=self._clean_text(handle),
            registrar=registrar,
            statuses=statuses,
            dates=DomainDates(
                registered_at=events.get("registration"),
                expires_at=events.get("expiration"),
                updated_at=events.get("last changed")
                or events.get("last update of rdap database"),
            ),
            nameservers=nameservers,
            dnssec=DNSSECInfo(enabled=delegation_signed),
            source="rdap",
            source_url=response.endpoint,
            fetched_at=response.fetched_at,
            parser_version=self.VERSION,
        )

    @staticmethod
    def _load_payload(response: RawLookupResponse) -> dict[str, Any]:
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ResponseParseError("RDAP 响应不是有效的 JSON") from exc
        if not isinstance(payload, dict):
            raise ResponseParseError("RDAP 响应的 JSON 根节点必须是对象")
        return payload

    @classmethod
    def _raise_for_error_response(
        cls,
        payload: Mapping[str, Any],
        status_code: int | None,
    ) -> None:
        error_code = payload.get("errorCode")
        is_http_error = status_code is not None and status_code >= 400
        if error_code is None and not is_http_error:
            return

        code = error_code if error_code is not None else status_code
        title = cls._clean_text(payload.get("title"))
        descriptions = payload.get("description")
        if isinstance(descriptions, list):
            detail = "; ".join(
                text
                for item in descriptions
                if (text := cls._clean_text(item)) is not None
            )
        else:
            detail = cls._clean_text(descriptions) or ""
        message = ": ".join(part for part in (title, detail) if part)
        suffix = f"：{message}" if message else ""
        raise ResponseParseError(f"RDAP 返回错误 {code}{suffix}")

    @classmethod
    def _parse_domain_name(
        cls,
        payload: Mapping[str, Any],
        domain: NormalizedDomain,
    ) -> str:
        raw_name = payload.get("ldhName") or payload.get("unicodeName")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ResponseParseError("RDAP domain 对象缺少 ldhName 或 unicodeName")
        response_name = cls._ascii_domain_name(raw_name, "domain name")
        expected_name = domain.registrable_domain.casefold().rstrip(".")
        if response_name != expected_name:
            raise ResponseParseError(
                f"RDAP 响应域名 {response_name!r} 与查询域名 {expected_name!r} 不一致"
            )
        return response_name

    @classmethod
    def _parse_events(cls, items: list[Any]) -> dict[str, datetime]:
        events: dict[str, datetime] = {}
        for item in items:
            if not isinstance(item, Mapping):
                continue
            action = cls._clean_text(item.get("eventAction"))
            event_date = cls._parse_datetime(item.get("eventDate"))
            if action is None or event_date is None:
                continue
            key = action.casefold()
            current = events.get(key)
            if current is None:
                events[key] = event_date
            elif key == "registration":
                events[key] = min(current, event_date)
            else:
                events[key] = max(current, event_date)
        return events

    @classmethod
    def _parse_nameservers(cls, items: list[Any]) -> list[str]:
        nameservers: set[str] = set()
        for item in items:
            if not isinstance(item, Mapping):
                continue
            raw_name = item.get("ldhName") or item.get("unicodeName")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            try:
                nameservers.add(cls._ascii_domain_name(raw_name, "nameserver"))
            except ResponseParseError:
                continue
        return sorted(nameservers)

    @classmethod
    def _parse_statuses(cls, items: list[Any]) -> list[str]:
        statuses: list[str] = []
        seen: set[str] = set()
        for item in items:
            status = cls._clean_text(item)
            if status is None:
                continue
            normalized = status.casefold()
            if normalized not in seen:
                seen.add(normalized)
                statuses.append(normalized)
        return statuses

    @classmethod
    def _parse_registrar(cls, entities: list[Any]) -> RegistrarInfo | None:
        all_entities = list(cls._walk_entities(entities))
        registrar = next(
            (
                item
                for item in all_entities
                if "registrar" in cls._roles(item.get("roles"))
            ),
            None,
        )
        if registrar is None:
            return None

        fields = cls._vcard_fields(registrar.get("vcardArray"))
        public_ids = registrar.get("publicIds")
        if not isinstance(public_ids, list):
            public_ids = []
        raw_iana_id = next(
            (
                item.get("identifier")
                for item in public_ids
                if isinstance(item, Mapping)
                and "iana" in str(item.get("type", "")).casefold()
            ),
            None,
        )
        try:
            iana_id = int(raw_iana_id) if raw_iana_id is not None else None
        except (TypeError, ValueError):
            iana_id = None

        registrar_children = registrar.get("entities")
        descendants = list(
            cls._walk_entities(
                registrar_children if isinstance(registrar_children, list) else []
            )
        )
        abuse = next(
            (
                item
                for item in descendants
                if "abuse" in cls._roles(item.get("roles"))
            ),
            None,
        ) or next(
            (
                item
                for item in all_entities
                if "abuse" in cls._roles(item.get("roles"))
            ),
            None,
        )
        abuse_fields = cls._vcard_fields(abuse.get("vcardArray")) if abuse else {}

        return RegistrarInfo(
            name=fields.get("fn") or cls._clean_text(registrar.get("handle")),
            iana_id=iana_id,
            url=fields.get("url") or cls._entity_url(registrar),
            abuse_email=cls._strip_uri_prefix(
                abuse_fields.get("email") or fields.get("email"), "mailto:"
            ),
            abuse_phone=cls._strip_uri_prefix(
                abuse_fields.get("tel") or fields.get("tel"), "tel:"
            ),
        )

    @classmethod
    def _walk_entities(
        cls, entities: Iterable[Any]
    ) -> Iterable[Mapping[str, Any]]:
        pending = list(reversed(list(entities)))
        while pending:
            entity = pending.pop()
            if not isinstance(entity, Mapping):
                continue
            yield entity
            children = entity.get("entities")
            if isinstance(children, list):
                pending.extend(reversed(children))

    @classmethod
    def _entity_url(cls, entity: Mapping[str, Any]) -> str | None:
        links = entity.get("links")
        if not isinstance(links, list):
            return None
        for link in links:
            if not isinstance(link, Mapping):
                continue
            href = cls._clean_text(link.get("href"))
            if href and href.lower().startswith(("http://", "https://")):
                return href
        return None

    @classmethod
    def _vcard_fields(cls, vcard: Any) -> dict[str, str]:
        if (
            not isinstance(vcard, list)
            or len(vcard) != 2
            or not isinstance(vcard[1], list)
        ):
            return {}
        result: dict[str, str] = {}
        for item in vcard[1]:
            if not isinstance(item, list) or len(item) < 4:
                continue
            key = cls._clean_text(item[0])
            value = cls._clean_text(item[3])
            if key and value:
                result.setdefault(key.casefold(), value)
        return result

    @staticmethod
    def _roles(value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {
            item.strip().casefold()
            for item in value
            if isinstance(item, str) and item.strip()
        }

    @staticmethod
    def _optional_list(payload: Mapping[str, Any], key: str) -> list[Any]:
        value = payload.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ResponseParseError(f"RDAP 字段 {key!r} 必须是数组")
        return value

    @staticmethod
    def _optional_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = payload.get(key)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ResponseParseError(f"RDAP 字段 {key!r} 必须是对象")
        return value

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _strip_uri_prefix(value: str | None, prefix: str) -> str | None:
        if value is None:
            return None
        if value.casefold().startswith(prefix):
            return value[len(prefix) :]
        return value

    @staticmethod
    def _ascii_domain_name(value: str, label: str) -> str:
        candidate = value.strip().rstrip(".")
        try:
            return idna.encode(candidate, uts46=True).decode("ascii").casefold()
        except idna.IDNAError as exc:
            raise ResponseParseError(f"RDAP {label} 不是有效域名：{value!r}") from exc

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = value.strip()
        if candidate.endswith(("Z", "z")):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        # RDAP 的 eventDate 是 RFC 3339 时间，必须包含 UTC 偏移。拒绝无时区值也能
        # 避免重复事件排序时比较 naive/aware datetime 导致 TypeError。
        return parsed if parsed.tzinfo is not None else None
