import idna
import tldextract

from modules.errors import DomainNormalizationError
from modules.models.domain import NormalizedDomain


class DomainNormalizer:
    """使用 IDNA 和 Public Suffix List 标准化域名，不执行网络请求。"""

    def __init__(self, extractor: tldextract.TLDExtract | None = None) -> None:
        self._extractor = extractor or tldextract.TLDExtract(
            suffix_list_urls=(),
            include_psl_private_domains=False,
        )

    def normalize(self, name: str) -> NormalizedDomain:
        input_name = name
        candidate = name.strip().lower().rstrip(".")
        if not candidate:
            raise DomainNormalizationError("域名不能为空")

        try:
            ascii_name = idna.encode(
                candidate,
                uts46=True,
                std3_rules=True,
            ).decode("ascii")
            unicode_name = idna.decode(ascii_name)
        except idna.IDNAError as exc:
            raise DomainNormalizationError(f"无效域名：{name!r}") from exc

        extracted = self._extractor(ascii_name)
        if not extracted.domain or not extracted.suffix:
            raise DomainNormalizationError(
                f"无法识别域名的 Public Suffix：{name!r}"
            )

        registrable_domain = extracted.top_domain_under_public_suffix
        return NormalizedDomain(
            input_name=input_name,
            ascii_name=ascii_name,
            unicode_name=unicode_name,
            subdomain=extracted.subdomain or None,
            domain_label=extracted.domain,
            public_suffix=extracted.suffix,
            registrable_domain=registrable_domain,
            tld=ascii_name.rsplit(".", 1)[-1],
        )
