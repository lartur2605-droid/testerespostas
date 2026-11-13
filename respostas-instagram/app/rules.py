"""Lógica pura (sem rede, sem banco) para decidir como tratar um comentário."""
import random
import re
import unicodedata

# Comentários com estes termos NUNCA recebem resposta automática: vão para a equipe.
# "palavra*" casa qualquer palavra que comece assim ("denunc*" -> denúncia, denunciar...).
DEFAULT_SENSITIVE = [
    "denunc*", "corrup*", "propina", "desvio de verba", "desvio de dinheiro", "fraud*",
    "roub*", "ladrao", "ladroes", "mentir*", "vergonh*", "incompeten*",
    "processar", "processo judicial", "justica", "advogad*", "ministerio publico",
    "promotoria", "imprensa", "ameac*", "assedi*", "abus*", "violenc*", "agress*",
    "estupr*", "morreu", "morrendo", "morte", "falec*", "suicid*", "me matar",
    "quero morrer", "socorro", "emergencia", "urgente", "urgencia",
]

_LINK_RE = re.compile(r"(https?://|www\.)", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def normalize(text: str) -> str:
    """minúsculas, sem acentos, só letras/números separados por um espaço."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def split_keywords(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n;]", raw or "") if p.strip()]


def _matches(padded: str, keyword: str) -> bool:
    prefix = keyword.strip().endswith("*")
    k = normalize(keyword)
    if not k:
        return False
    return (f" {k}" in padded) if prefix else (f" {k} " in padded)


def find_keyword(text: str, keywords: list[str]) -> str | None:
    padded = f" {normalize(text)} "
    for kw in keywords:
        if _matches(padded, kw):
            return kw
    return None


def find_sensitive(text: str, extra: str = "") -> str | None:
    return find_keyword(text, DEFAULT_SENSITIVE + split_keywords(extra))


def has_letters(text: str) -> bool:
    return any(c.isalpha() for c in (text or ""))


def contains_link(text: str) -> bool:
    return bool(_LINK_RE.search(text or ""))


def contains_personal_data(text: str) -> bool:
    """CPF, telefone (10+ dígitos) ou e-mail no comentário. Não respondemos: LGPD."""
    if _EMAIL_RE.search(text or ""):
        return True
    digits = re.sub(r"[\s().\-+]", "", text or "")
    return bool(re.search(r"\d{10,}", digits))


def match_rule(text: str, rules) -> tuple[object | None, str | None]:
    """Primeira regra (por prioridade) cuja palavra-chave aparece no texto."""
    for rule in rules:
        kw = find_keyword(text, split_keywords(rule["keywords"]))
        if kw:
            return rule, kw
    return None, None


def render_response(responses: str, username: str = "", contact: str = "") -> str:
    """Sorteia uma das variações (uma por linha) e preenche {usuario} e {contato}."""
    options = [ln.strip() for ln in (responses or "").splitlines() if ln.strip()]
    if not options:
        return ""
    text = random.choice(options)
    handle = f"@{username}" if username else ""
    return text.replace("{usuario}", handle).replace("{contato}", contact or "").strip()
