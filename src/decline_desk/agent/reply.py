"""Reply templates and the reply checker.

Templates cover every outcome in es/pt/en. They are the zero-cost path, and the fallback
whenever a model-drafted reply fails the checker. The checker blocks four things a support
reply must never do: leak a card number, promise an action that is only pending approval,
reveal internal fraud detail, or come back empty or rambling.
"""

import re
from zoneinfo import ZoneInfo

from decline_desk.domain.models import Authorization
from decline_desk.pii import contains_card_number

LANGUAGES = ("es", "pt", "en")
MAX_REPLY_CHARS = 1500

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "ask_details": "Thanks for reaching out. Could you tell us which purchase was declined "
        "(merchant, approximate amount and when)?",
        "forwarded": "Thanks, we've passed your question to our support team. They'll reply here.",
        "needs_human": "Thanks, a member of our team will look at this and reply shortly.",
        "clarify": "We found more than one declined payment. Which one do you mean?",
        "not_found": "We can't find this attempt on our side, so it never reached us. The "
        "merchant's terminal or website likely failed before sending it. Please try again or "
        "use another payment method at that merchant.",
        "approved_not_declined": "Good news: this payment was approved on our side. If the "
        "merchant still shows a problem, they may need to retry or refresh their system.",
        "escalated_security": "For your protection this payment was paused for a security "
        "check. Our security team will contact you directly. Please don't retry it for now.",
        "escalated_review": "Thanks, we need to look into this one more closely. Our team "
        "will review it and get back to you.",
        "action_requested": "We've asked your company admin to approve: {action}. You'll be "
        "notified once it's decided.",
        "card_expired": "This card has expired. Please use your new card, or ask your admin "
        "to issue one.",
        "card_frozen_by_user": "This card is currently frozen. You can unfreeze it in the "
        "app, or we can ask your admin to do it.",
        "country_restricted": "Your card is only enabled for {allowed}, and this purchase was "
        "in {country}.",
        "mcc_blocked": "Your company doesn't allow this type of merchant on your card.",
        "limit_per_txn": "This purchase was above your per-transaction limit of ${limit}.",
        "limit_daily": "This purchase would have taken you over your daily limit of ${limit}.",
        "company_funds_low": "Your company account didn't have enough balance for this "
        "purchase. Your finance team has been told.",
        "funds_pending_settlement": "Your company's account top-up is still settling and "
        "should be available around {eta}. Please try again after that.",
        "three_ds_failed": "The bank's security check (3-D Secure) wasn't completed. Please "
        "try again and approve the check in your banking app or by SMS code.",
        "incorrect_cvv": "The security code (CVV) entered didn't match. Please check the "
        "3 digits on your card and try again.",
    },
    "es": {
        "ask_details": "Gracias por escribirnos. ¿Nos dices qué compra fue rechazada "
        "(comercio, monto aproximado y cuándo)?",
        "forwarded": "Gracias, pasamos tu consulta a nuestro equipo de soporte. Te responderán "
        "aquí.",
        "needs_human": "Gracias, una persona de nuestro equipo revisará esto y te responderá "
        "pronto.",
        "clarify": "Encontramos más de un pago rechazado. ¿A cuál te refieres?",
        "not_found": "No encontramos este intento de nuestro lado, así que nunca nos llegó. "
        "Probablemente falló el datáfono o la web del comercio antes de enviarlo. Intenta de "
        "nuevo o usa otro medio de pago en ese comercio.",
        "approved_not_declined": "Buenas noticias: este pago fue aprobado de nuestro lado. Si "
        "el comercio aún muestra un problema, quizá deba reintentar o actualizar su sistema.",
        "escalated_security": "Por tu seguridad, este pago quedó en pausa para una "
        "verificación. Nuestro equipo de seguridad te contactará directamente. Por ahora no lo "
        "reintentes.",
        "escalated_review": "Gracias, necesitamos revisar este caso con más detalle. Nuestro "
        "equipo lo revisará y te responderá.",
        "action_requested": "Pedimos a tu administrador que apruebe: {action}. Te avisaremos "
        "cuando lo decida.",
        "card_expired": "Esta tarjeta está vencida. Usa tu tarjeta nueva o pide a tu "
        "administrador que emita una.",
        "card_frozen_by_user": "Esta tarjeta está congelada. Puedes descongelarla en la app o "
        "podemos pedírselo a tu administrador.",
        "country_restricted": "Tu tarjeta solo está habilitada para {allowed}, y esta compra "
        "fue en {country}.",
        "mcc_blocked": "Tu empresa no permite este tipo de comercio en tu tarjeta.",
        "limit_per_txn": "Esta compra superó tu límite por transacción de ${limit}.",
        "limit_daily": "Esta compra habría superado tu límite diario de ${limit}.",
        "company_funds_low": "La cuenta de tu empresa no tenía saldo suficiente para esta "
        "compra. Ya avisamos a tu equipo de finanzas.",
        "funds_pending_settlement": "La recarga de la cuenta de tu empresa aún se está "
        "acreditando y debería estar disponible hacia las {eta}. Intenta de nuevo después.",
        "three_ds_failed": "No se completó la verificación de seguridad del banco (3-D "
        "Secure). Intenta de nuevo y aprueba la verificación en tu app o con el código SMS.",
        "incorrect_cvv": "El código de seguridad (CVV) ingresado no coincide. Revisa los 3 "
        "dígitos de tu tarjeta e intenta de nuevo.",
    },
    "pt": {
        "ask_details": "Obrigado pelo contato. Pode nos dizer qual compra foi recusada "
        "(estabelecimento, valor aproximado e quando)?",
        "forwarded": "Obrigado, encaminhamos sua dúvida ao nosso time de suporte. Eles vão "
        "responder aqui.",
        "needs_human": "Obrigado, alguém do nosso time vai analisar e responder em breve.",
        "clarify": "Encontramos mais de um pagamento recusado. Qual deles você quer dizer?",
        "not_found": "Não encontramos essa tentativa do nosso lado, então ela nunca chegou até "
        "nós. Provavelmente a maquininha ou o site do estabelecimento falhou antes de enviá-la. "
        "Tente de novo ou use outro meio de pagamento nesse estabelecimento.",
        "approved_not_declined": "Boa notícia: este pagamento foi aprovado do nosso lado. Se o "
        "estabelecimento ainda mostrar um problema, talvez precise tentar de novo ou atualizar "
        "o sistema.",
        "escalated_security": "Para sua proteção, este pagamento foi pausado para uma "
        "verificação. Nosso time de segurança vai entrar em contato diretamente. Por enquanto, "
        "não tente de novo.",
        "escalated_review": "Obrigado, precisamos analisar este caso com mais detalhe. Nosso "
        "time vai revisar e retornar.",
        "action_requested": "Pedimos ao administrador da sua empresa que aprove: {action}. "
        "Você será avisado quando for decidido.",
        "card_expired": "Este cartão está vencido. Use seu cartão novo ou peça ao "
        "administrador para emitir um.",
        "card_frozen_by_user": "Este cartão está congelado. Você pode descongelá-lo no app, ou "
        "podemos pedir ao administrador.",
        "country_restricted": "Seu cartão só está habilitado para {allowed}, e esta compra foi "
        "em {country}.",
        "mcc_blocked": "Sua empresa não permite este tipo de estabelecimento no seu cartão.",
        "limit_per_txn": "Esta compra passou do seu limite por transação de ${limit}.",
        "limit_daily": "Esta compra teria passado do seu limite diário de ${limit}.",
        "company_funds_low": "A conta da sua empresa não tinha saldo suficiente para esta "
        "compra. Já avisamos o time financeiro.",
        "funds_pending_settlement": "A recarga da conta da sua empresa ainda está sendo "
        "compensada e deve estar disponível por volta das {eta}. Tente de novo depois disso.",
        "three_ds_failed": "A verificação de segurança do banco (3-D Secure) não foi "
        "concluída. Tente de novo e aprove a verificação no app do banco ou pelo código SMS.",
        "incorrect_cvv": "O código de segurança (CVV) digitado não confere. Confira os 3 "
        "dígitos do cartão e tente de novo.",
    },
}


def render(lang: str, key: str, **values: str) -> str:
    """Fill a template. A missing placeholder raises KeyError rather than sending '{eta}'."""
    return TEMPLATES[lang][key].format(**values)


def clarifying_question(lang: str, candidates: list[Authorization], *, timezone: str) -> str:
    tz = ZoneInfo(timezone)
    lines = [TEMPLATES[lang]["clarify"]]
    for a in candidates:
        when = a.created_at.astimezone(tz).strftime("%d/%m %H:%M")
        lines.append(f"- {a.merchant_name}: {a.amount} {a.currency.value}, {when}")
    return "\n".join(lines)


_PROMISE = re.compile(
    r"\b(ya |já )?(aumentamos|habilitamos|desbloqueamos|subimos|liberamos|descongelamos)\b"
    r"|\bhemos (aumentado|habilitado|desbloqueado|subido|descongelado)\b"
    r"|\bwe(?:'ve| have)? (increased|raised|unblocked|enabled|unfrozen|allowed)\b"
    r"|\bhas been (increased|raised|unblocked|enabled|unfrozen|allowed)\b",
    re.IGNORECASE,
)
_INTERNAL = re.compile(
    r"risk (score|model)|fraud rule|puntaje de riesgo|regla de fraude|score de risco"
    r"|regra de fraude|modelo de riesgo|modelo de risco",
    re.IGNORECASE,
)


def verify_reply(text: str) -> list[str]:
    """Problems that make a reply unsafe to send. An empty list means it passes."""
    problems: list[str] = []
    if not text.strip():
        problems.append("empty")
    if len(text) > MAX_REPLY_CHARS:
        problems.append("too_long")
    if contains_card_number(text):
        problems.append("card_number")
    if _PROMISE.search(text):
        problems.append("premature_promise")
    if _INTERNAL.search(text):
        problems.append("internal_detail")
    return problems
