import os
import requests
import pandas as pd
import io

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
URL = "https://myside.com.br/guia-goiania/lancamentos-imobiliarios-goiania-go"
ARQUIVO_DADOS = 'lancamentos.csv'

def send_telegram_message(text):
    url_api = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    requests.post(url_api, json=payload)

def main():
    # Simulando um navegador padrão para evitar bloqueios
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(URL, headers=headers)
    
    try:
        # O pandas extrai todas as tags <table> do HTML automaticamente
        tabelas = pd.read_html(io.StringIO(response.text))
        
        # Junta todas as tabelas do artigo num único DataFrame e limpa os dados
        df_atual = pd.concat(tabelas, ignore_index=True)
        df_atual = df_atual.astype(str)
    except Exception as e:
        print(f"Erro ao processar as tabelas: {e}")
        return

    # Verifica se já temos uma base salva da última execução
    if os.path.exists(ARQUIVO_DADOS):
        df_antigo = pd.read_csv(ARQUIVO_DADOS).astype(str)
        
        # Cruza os dados para achar as linhas que estão no site hoje, mas não estavam ontem
        df_merge = df_atual.merge(df_antigo, how='left', indicator=True)
        novos_lancamentos = df_merge[df_merge['_merge'] == 'left_only'].drop(columns=['_merge'])
        
        if not novos_lancamentos.empty:
            mensagem = "🏢 <b>Novos Lançamentos Detectados!</b>\n\n"
            
            for _, row in novos_lancamentos.iterrows():
                # Pega os 3 primeiros valores da linha (geralmente Nome, Setor e Data)
                valores = row.dropna().tolist()
                detalhes = " - ".join(valores[:3])
                mensagem += f"• {detalhes}\n"
            
            mensagem += f"\n🔗 <a href='{URL}'>Acessar Calendário</a>"
            
            send_telegram_message(mensagem)
            print("Novos lançamentos encontrados. Telegram enviado.")
            
            # Sobrescreve o CSV com o cenário atualizado
            df_atual.to_csv(ARQUIVO_DADOS, index=False)
        else:
            print("Nenhum lançamento novo identificado hoje.")
    else:
        # Primeira vez que o script roda
        df_atual.to_csv(ARQUIVO_DADOS, index=False)
        qtd = len(df_atual)
        send_telegram_message(f"✅ <b>Monitoramento Iniciado!</b>\nForam mapeados {qtd} lançamentos imobiliários na base inicial.")
        print("Arquivo CSV inicial criado.")

if __name__ == "__main__":
    if TOKEN and CHAT_ID:
        main()
    else:
        print("Erro: Variáveis de ambiente do Telegram não configuradas.")
