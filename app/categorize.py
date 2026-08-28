import json
from pathlib import Path

import yaml

from google import genai
from google.genai import types

RULES_PATH = Path(__file__).resolve().parent / "categories.yaml"

# Definições enviadas ao modelo para reduzir "outros" a um genuíno resto.
CATEGORY_DEFINITIONS = {
    "restaurantes": "refeições fora de casa: restaurantes, cafés, pastelarias, bares, take-away e entregas de comida (Uber Eats, Glovo, Bolt Food)",
    "transporte": "deslocações: combustível, portagens, transportes públicos (CP, Metro, Carris), táxi/TVDE (Uber, Bolt), estacionamento, aluguer de trotinetes/bicicletas",
    "compras": "retalho de bens: supermercado e mercearia (Continente, Pingo Doce, Lidl), lojas físicas e online, vestuário, eletrónica, artigos para casa (Amazon, Worten, IKEA, Zara)",
    "subscricoes": "serviços recorrentes: streaming, software, cloud, ginásio, telecomunicações, jornais e revistas",
    "saude": "farmácia, consultas, clínicas, hospitais, dentista, ótica, seguros de saúde, análises",
    "outros": "APENAS quando não encaixa em nenhuma acima: transferências entre contas próprias, envios P2P, levantamentos ATM, salário e rendimentos, impostos, taxas bancárias, doações",
}


def load_rules() -> dict[str, list[str]]:
    return yaml.safe_load(RULES_PATH.read_text())


def valid_categories() -> list[str]:
    return list(load_rules().keys()) + ["outros"]


def categorize_rule(description: str) -> tuple[str, str] | None:
    """Devolve (categoria, keyword_que_deu_match) ou None."""
    if not description:
        return None
    desc = description.lower()
    for category, keywords in load_rules().items():
        for kw in keywords:
            if kw in desc:
                return category, kw
    return None


def merchant_text(txn: dict) -> str:
    """Junta nome do comerciante (de raw_json) com a descrição, para dar mais contexto ao modelo."""
    parts = []
    raw = txn.get("raw_json") or txn.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, dict):
        for key in ("creditor", "debtor", "creditor_agent", "debtor_agent"):
            name = (raw.get(key) or {}).get("name")
            if name:
                parts.append(name)
    if txn.get("description"):
        parts.append(txn["description"])
    return " · ".join(dict.fromkeys(p.strip() for p in parts if p)) or (txn.get("description") or "")


def categorize_llm_fallback(transactions: list[dict]) -> dict[int, tuple[str, str]]:
    """transactions: [{"id": int, "text": str}, ...] sem match de regra.
    Devolve {id: (categoria, justificação)} num único pedido em batch."""
    if not transactions:
        return {}

    categories = valid_categories()
    definitions = "\n".join(f"- {name}: {desc}" for name, desc in CATEGORY_DEFINITIONS.items())
    client = genai.Client()
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=(
            "És um classificador de transações bancárias pessoais em Portugal. "
            "Atribui a cada transação exatamente uma categoria da lista, segundo estas definições:\n"
            f"{definitions}\n\n"
            "Regras: escolhe sempre a categoria mais específica possível; só usa 'outros' se "
            "realmente não encaixar em mais nada. Para cada transação dá uma justificação curta "
            "em português europeu (máximo 10 palavras); se for 'outros', a justificação tem de "
            "dizer o que é a transação.\n\n"
            "Transações:\n" + json.dumps(transactions, ensure_ascii=False)
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "categorizations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "category": {"type": "string", "enum": categories},
                                "rationale": {"type": "string"},
                            },
                            "required": ["id", "category", "rationale"],
                        },
                    },
                },
                "required": ["categorizations"],
            },
        ),
    )
    data = json.loads(response.text)
    return {item["id"]: (item["category"], item["rationale"]) for item in data["categorizations"]}
