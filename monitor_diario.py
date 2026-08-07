"""Monitor semanal do Diário Oficial de Goiânia.

Roda toda segunda-feira: pega os diários publicados nos últimos 7 dias que ainda
não foram lidos, filtra as páginas que citam os termos monitorados e pede à IA
(Gemini com fallback Mistral) o resumo das certidões encontradas.

O controle do que já foi lido fica em ultimo_diario.txt (um arquivo PDF por
linha), commitado pelo workflow — é isso que impede repetir informação.
"""

import datetime
import io
import os
import re

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

URL_BASE = "https://www.goiania.go.gov.br"
URL_LISTA = URL_BASE + "/shtml//portal/casacivil/lista_diarios.asp?ano={ano}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

ARQUIVO_HISTORICO = "ultimo_diario.txt"
MAX_HISTORICO = 200
TERMOS = ("remembramento", "desmembramento")
LIMITE_CHARS = 120_000

FUSO_BRT = datetime.timezone(datetime.timedelta(hours=-3))
# do_20260806_000008836.pdf -> data + nome do arquivo
PADRAO_PDF = re.compile(r"do_(\d{8})_[\w-]*\.pdf$", re.I)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
MISTRAL_API_KEYS = [k.strip() for k in os.environ.get("MISTRAL_API_KEYS", "").split(",") if k.strip()]

DIAS_JANELA = int(os.environ.get("DIAS_JANELA") or 7)
IGNORAR_HISTORICO = os.environ.get("IGNORAR_HISTORICO", "").lower() in ("1", "true", "yes")

PROMPT_IA = """Analise o trecho do Diário Oficial de Goiânia abaixo e liste APENAS as
certidões de REMEMBRAMENTO ou DESMEMBRAMENTO.

Para cada uma, extraia:
1. Interessado
2. Endereço/localização do imóvel
3. Tipo (Remembramento ou Desmembramento) + resumo da decisão

Responda SÓ com os registros, sem introdução, sem markdown e sem blocos de código.
Use exatamente este formato HTML do Telegram para cada certidão:
🏢 <b>Interessado:</b> <i>Nome</i>
📍 <b>Local:</b> <i>Endereço</i>
📝 <b>Decisão:</b> <i>Tipo — resumo</i>
------------------------

Se não encontrar nenhuma certidão desses dois tipos, responda exatamente: NADA
"""


# ==========================================
# TELEGRAM
# ==========================================
def _quebrar(texto, limite=3900):
    """Quebra em blocos respeitando as linhas, para não partir uma tag HTML no meio."""
    blocos, atual = [], ""
    for linha in texto.split("\n"):
        linha = linha[:limite]
        if len(atual) + len(linha) + 1 > limite:
            blocos.append(atual)
            atual = ""
        atual += linha + "\n"
    return [b for b in blocos + [atual] if b.strip()]


def enviar_telegram(texto):
    """Envia a mensagem quebrando em partes se for maior que o limite do Telegram."""
    if not texto:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for bloco in _quebrar(texto):
        try:
            resposta = requests.post(
                url, json={"chat_id": TELEGRAM_CHAT_ID, "text": bloco, "parse_mode": "HTML"}, timeout=30
            )
            if not resposta.ok:
                # Se a IA devolveu HTML que o Telegram não aceita, reenvia como texto puro.
                print(f"Erro no envio HTML: {resposta.status_code} {resposta.text[:200]}")
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": bloco}, timeout=30)
        except Exception as e:
            print(f"Erro ao enviar para Telegram: {e}")


# ==========================================
# HISTÓRICO DE DIÁRIOS JÁ LIDOS
# ==========================================
def carregar_historico():
    if not os.path.exists(ARQUIVO_HISTORICO):
        return set()
    with open(ARQUIVO_HISTORICO, encoding="utf-8") as f:
        return {linha.strip() for linha in f if linha.strip()}


def salvar_historico(historico):
    with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(historico)[-MAX_HISTORICO:]) + "\n")


# ==========================================
# COLETA DOS DIÁRIOS
# ==========================================
def listar_diarios(desde, ate):
    """Retorna [(data, nome_arquivo, url)] dos diários publicados a partir de `desde`."""
    diarios = {}
    for ano in sorted({desde.year, ate.year}):
        resposta = requests.get(URL_LISTA.format(ano=ano), headers=HEADERS, timeout=60)
        resposta.raise_for_status()
        for link in BeautifulSoup(resposta.text, "html.parser").find_all("a", href=True):
            achado = PADRAO_PDF.search(link["href"])
            if not achado:
                continue
            data = datetime.datetime.strptime(achado.group(1), "%Y%m%d").date()
            if data >= desde:
                nome = achado.group(0)
                diarios[nome] = (data, nome, requests.compat.urljoin(URL_BASE, link["href"]))
    return sorted(diarios.values())


def _baixar(url):
    resposta = requests.get(url, headers=HEADERS, timeout=180)
    resposta.raise_for_status()
    return resposta.content


def extrair_trechos_relevantes(conteudo_pdf):
    """Extrai só as páginas que citam os termos (mais a seguinte, pois a certidão pode virar a página)."""
    relevantes = []
    guardar_proxima = False
    for pagina in PdfReader(io.BytesIO(conteudo_pdf)).pages:
        texto = pagina.extract_text() or ""
        tem_termo = any(termo in texto.lower() for termo in TERMOS)
        if tem_termo or guardar_proxima:
            relevantes.append(texto)
        guardar_proxima = tem_termo
    return "\n".join(relevantes)[:LIMITE_CHARS]


# ==========================================
# IA: GEMINI COM FALLBACK MISTRAL
# ==========================================
def _chamar_gemini(prompt):
    import google.generativeai as genai

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY não configurada")
    genai.configure(api_key=GOOGLE_API_KEY)
    return genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt).text


def _chamar_mistral(prompt):
    """Tenta cada chave da lista até uma responder (as chaves gratuitas estouram cota fácil)."""
    if not MISTRAL_API_KEYS:
        raise RuntimeError("MISTRAL_API_KEYS não configurada")
    ultimo_erro = None
    for chave in MISTRAL_API_KEYS:
        try:
            resposta = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {chave}", "Content-Type": "application/json"},
                json={
                    "model": "mistral-large-latest",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
                timeout=180,
            )
            resposta.raise_for_status()
            return resposta.json()["choices"][0]["message"]["content"]
        except Exception as e:
            ultimo_erro = e
            print(f"   ↳ chave Mistral ...{chave[-4:]} falhou: {e}")
    raise RuntimeError(f"todas as {len(MISTRAL_API_KEYS)} chaves Mistral falharam: {ultimo_erro}")


def analisar(trecho):
    prompt = f"{PROMPT_IA}\n\n{trecho}"
    for nome, chamar in (("Gemini", _chamar_gemini), ("Mistral", _chamar_mistral)):
        try:
            texto = chamar(prompt)
            if texto and texto.strip():
                print(f"   ✅ análise via {nome}")
                return texto.strip()
            print(f"   ⚠️ {nome} devolveu resposta vazia")
        except Exception as e:
            print(f"   ⚠️ {nome} falhou: {e}")
    return None


# ==========================================
# FLUXO PRINCIPAL
# ==========================================
def main():
    hoje = datetime.datetime.now(FUSO_BRT).date()
    desde = hoje - datetime.timedelta(days=DIAS_JANELA)
    historico = set() if IGNORAR_HISTORICO else carregar_historico()
    cabecalho = f"🏛️ <b>Diário Oficial de Goiânia</b>\nPeríodo: {desde:%d/%m} a {hoje:%d/%m}"

    print(f"IA: Gemini {'ok' if GOOGLE_API_KEY else 'ausente'} | "
          f"fallback Mistral: {len(MISTRAL_API_KEYS)} chave(s)")
    print(f"Buscando diários de {desde:%d/%m/%Y} até {hoje:%d/%m/%Y}...")
    novos = [d for d in listar_diarios(desde, hoje) if d[1] not in historico]
    if not novos:
        print("Nenhum diário novo no período.")
        enviar_telegram(f"{cabecalho}\n\nℹ️ Nenhuma edição nova para analisar.")
        return

    print(f"{len(novos)} diário(s) novo(s): {', '.join(n for _, n, _ in novos)}")
    achados = []
    lidos = []
    for data, nome, url in novos:
        print(f"\n📄 {data:%d/%m/%Y} — {nome}")
        try:
            # Sem nomear o Response: os ~6 MB do PDF são liberados antes da chamada à IA.
            trecho = extrair_trechos_relevantes(_baixar(url))
        except Exception as e:
            print(f"   ❌ falha ao ler o PDF: {e}")
            continue

        lidos.append(nome)
        if not trecho.strip():
            print("   ↳ nenhum termo monitorado nesta edição")
            continue

        print(f"   🔎 {len(trecho)} caracteres relevantes, enviando para a IA...")
        analise = analisar(trecho)
        if not analise:
            achados.append(f"⚠️ <b>{data:%d/%m/%Y}</b> — termos encontrados, mas a IA não respondeu.\n"
                           f"🔗 <a href='{url}'>Abrir PDF</a>")
        elif analise.strip(" .\n").upper() != "NADA":
            achados.append(f"📅 <b>{data:%d/%m/%Y}</b> — <a href='{url}'>PDF</a>\n\n{analise}")

    corpo = "\n\n".join(achados) if achados else "ℹ️ Nenhum remembramento ou desmembramento no período."
    enviar_telegram(f"{cabecalho} — {len(lidos)} edição(ões) lida(s)\n\n{corpo}")

    if IGNORAR_HISTORICO:
        print("Modo teste: histórico não foi atualizado.")
    else:
        salvar_historico(historico | set(lidos))
    print(f"\n✅ Processo finalizado. {len(lidos)} edição(ões) lida(s), {len(achados)} com achados.")


if __name__ == "__main__":
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("Erro: configure os secrets TELEGRAM_TOKEN e TELEGRAM_CHAT_ID.")
        raise SystemExit(1)
    if not (GOOGLE_API_KEY or MISTRAL_API_KEYS):
        print("Erro: configure GOOGLE_API_KEY e/ou MISTRAL_API_KEYS.")
        raise SystemExit(1)
    try:
        main()
    except Exception as e:
        print(f"❌ Erro crítico: {e}")
        enviar_telegram(f"❌ <b>Erro no monitor do Diário Oficial</b>\n{e}")
        raise
