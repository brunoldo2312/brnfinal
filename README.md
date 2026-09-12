📘 Manual Completo — BRN RWA
Plataforma de Tokenização de Ativos Financeiros
Versão: 1.0
Última atualização: 12/09/2026
Público-alvo: operadores, emissores, auditores e desenvolvedores

📑 Índice
Visão geral

Arquitetura

Pré-requisitos

Instalação

Configuração (.env)

Execução do nó

Interface web

Operações — guia prático

Segurança

Backup e recuperação

Troubleshooting

Referência de arquivos

Compliance e auditoria

Produção

1. Visão geral
O que é
A BRN RWA é uma blockchain privada/consórcio construída para tokenizar ativos do mundo real (ações, bonds, imóveis, commodities, recebíveis) com:

Multi-ativo: uma cadeia, vários ativos (BRN, PETR4, OURO, RE-001, …)

KYC/AML on-chain: cada endereço tem um status de compliance

Transferências restritas: só entre endereços aprovados (por ativo)

Emissor autorizado: cada ativo tem um issuer e um transfer_agent

Dividendos automáticos: distribuição proporcional aos holders

Finality gadget: blocos finalizados não podem ser revertidos

Slashing: validadores maliciosos perdem o stake com evidência assinada

Casos de uso
Caso	Ativo	Compliance
Ações tokenizadas	equity	KYC obrigatório, jurisdição BR/EU/US
Títulos de dívida	bond	KYC institucional
Imóveis fracionados	real_estate	KYC + lockup 2 anos
Ouro como reserva	commodity	KYC básico
Créditos de carbono	carbon	KYC + jurisdição restrita
O que não é
Não é pública (não é Bitcoin/Ethereum) — os validadores são autorizados

Não é anônima — todo participante passa por KYC

Não é descentralizada como uma L1 pública — é um consórcio permissionado

2. Arquitetura
text
┌────────────────────────────────────────────────────────────────┐
│                       OPERADOR / EMISSOR                        │
│  CMD + Python CLI  │  Painel Web  │  GUI pywebview             │
└────────────┬───────────────────┬────────────────┬──────────────┘
             │                   │                │
             ▼                   ▼                ▼
    ┌──────────────────────────────────────────────────┐
    │              NÓ P2P (node.py)                     │
    │  ┌────────────┐  ┌──────────────┐  ┌──────────┐  │
    │  │ Consenso   │  │ Mempool      │  │ Registry │  │
    │  │ PoS+Finality│ │ (SQLite)     │  │ RWA      │  │
    │  └────────────┘  └──────────────┘  └──────────┘  │
    │  ┌────────────┐  ┌──────────────┐  ┌──────────┐  │
    │  │ Slashing   │  │ Faucet       │  │ KYC/AML  │  │
    │  └────────────┘  └──────────────┘  └──────────┘  │
    └────────┬─────────────────────────┬───────────────┘
             │ UDP :7777               │ HTTP :5000
             ▼                         ▼
    ┌──────────────────┐      ┌────────────────────┐
    │  OUTROS NÓS      │      │  Painel Web Flask  │
    │  (consórcio)     │      │  + Basic Auth      │
    └──────────────────┘      │  + Rate limit      │
                              └────────────────────┘
Componentes
Arquivo	Função
bruno_blockchain_real.py	Núcleo: blocos, txs, estado, consenso, finality, slashing
assets.py	Definições de ativos, KYC, regras de transferência
node.py	Nó P2P: UDP, consenso, bootstrap de identidade
web_server.py	API REST + dashboard Flask
cripto_wallet.py	ECDSA + AES-256-GCM + Argon2id
index.html	GUI desktop (via pywebview)
blockchain.db	SQLite: cadeia, mempool, slashing, faucet, registry
*.wallet	Carteiras cifradas do nó (identidade, faucet)
.env	Configuração (não versionar!)
3. Pré-requisitos
Hardware mínimo
Componente	Mínimo	Recomendado
CPU	2 cores	4+ cores
RAM	2 GB	8 GB
Disco	1 GB SSD	50 GB SSD
Rede	10 Mbps	100 Mbps
Software
Software	Versão	Notas
Python	3.10+	Obrigatório
Windows	10/11	Linux/macOS também suportado
Chrome/Edge	Recente	Para o painel
Verificar o Python
cmd
python --version
Esperado: Python 3.10.x ou superior.

Se não tiver, baixe em https://python.org/downloads e marque Add Python to PATH durante a instalação.

Verificar pip
cmd
pip --version
4. Instalação
4.1 Criar a pasta do projeto
cmd
mkdir "C:\brn-rwa"
cd "C:\brn-rwa"
(Substitua o caminho pelo que preferir.)

4.2 Copiar os arquivos do projeto
Coloque na pasta todos estes arquivos:

text
brn-rwa/
├── bruno_blockchain_real.py
├── assets.py
├── node.py
├── web_server.py
├── cripto_wallet.py
├── index.html
├── app_wallet.py       ← opcional (GUI)
├── requirements.txt
├── .env.example
└── .gitignore
⚠️ Não copie blockchain.db, *.wallet nem .env de outro projeto — serão gerados automaticamente.

4.3 Criar ambiente virtual (venv)
Isolar as dependências evita conflito com outros projetos:

cmd
python -m venv .venv
.venv\Scripts\activate
O prompt muda para:

text
(.venv) C:\brn-rwa>
Sempre que reabrir o CMD para trabalhar no projeto, reative:

cmd
cd "C:\brn-rwa"
.venv\Scripts\activate
4.4 Instalar dependências
cmd
pip install --upgrade pip
pip install -r requirements.txt
Se o requirements.txt não estiver completo, use:

cmd
pip install python-dotenv flask flask-limiter pyngrok cryptography argon2-cffi ecdsa pywebview requests
Tempo estimado: 1–3 minutos.

4.5 Verificar a instalação
cmd
python -c "import flask, dotenv, cryptography, argon2, ecdsa, requests; print('OK: tudo instalado')"
Esperado: OK: tudo instalado

5. Configuração (.env)
5.1 Criar o arquivo .env
Atenção: o .env é um arquivo de texto sem nome, só extensão (.env, não .env.txt).

No CMD, cole esta linha única:

cmd
python -c "open('.env','w').write('BRN_NETWORK_ID=brn-rwa-1\nBRN_P2P_HOST=0.0.0.0\nBRN_P2P_PORT=7777\nBRN_TARGET_BLOCK_TIME=10\nBRN_WEB_HOST=0.0.0.0\nBRN_WEB_PORT=5000\nBRN_WEB_USER=admin\nBRN_WEB_PASS=TROQUE_ESTA_SENHA\nBRN_USE_NGROK=0\nBRN_DB_PATH=blockchain.db\nBRN_IDENTITY_FILE=node_identity.wallet\nBRN_FAUCET_KEY_FILE=faucet_identity.wallet\nBRN_MASTER_PASSWORD=TROQUE-ESTA-SENHA-LONGA-1234567890\nBRN_BLOCK_REWARD=1\nBRN_MIN_STAKE=100\nBRN_FINALITY_INTERVAL=5\nBRN_FINALITY_THRESHOLD=0.67\nBRN_FAUCET_AMOUNT=100\nBRN_FAUCET_COOLDOWN=3600\nBRN_FAUCET_MAX_PER_IP=3\n')" && echo .env CRIADO
5.2 Editar com valores reais
cmd
notepad .env
Substitua:

Variável	Valor	Regra
BRN_WEB_PASS	senha do painel	≥ 12 caracteres
BRN_MASTER_PASSWORD	senha mestra das carteiras	≥ 20 caracteres
BRN_REGULATOR_ADDRESS	endereço do regulador (opcional)	brn1...
5.3 Referência completa das variáveis
Variável	Default	Descrição
BRN_NETWORK_ID	brn-rwa-1	Identificador da rede (mesmo valor em todos os nós)
BRN_P2P_HOST	0.0.0.0	Interface UDP do P2P
BRN_P2P_PORT	7777	Porta UDP do P2P
BRN_SEED_PEERS	vazio	Lista host:porta,host:porta de peers iniciais
BRN_TARGET_BLOCK_TIME	10	Segundos entre blocos
BRN_WEB_HOST	0.0.0.0	Interface HTTP
BRN_WEB_PORT	5000	Porta HTTP
BRN_WEB_USER	admin	Usuário do painel
BRN_WEB_PASS	(vazio)	Senha do painel
BRN_USE_NGROK	0	1 ativa túnel público
NGROK_AUTHTOKEN	(vazio)	Token do ngrok
BRN_DB_PATH	blockchain.db	Caminho do SQLite
BRN_IDENTITY_FILE	node_identity.wallet	Carteira do nó
BRN_FAUCET_KEY_FILE	faucet_identity.wallet	Carteira do faucet
BRN_MASTER_PASSWORD	—	Senha que cifra as carteiras do nó (≥ 20 chars)
BRN_BLOCK_REWARD	1	Recompensa por bloco (BRN)
BRN_MIN_STAKE	100	Stake mínimo para validar
BRN_FINALITY_INTERVAL	5	Blocos entre checkpoints
BRN_FINALITY_THRESHOLD	0.67	% de stake para finalizar
BRN_FAUCET_AMOUNT	100	BRN por claim
BRN_FAUCET_COOLDOWN	3600	Segundos entre claims do mesmo IP
BRN_FAUCET_MAX_PER_IP	3	Claims por IP por hora
BRN_REGULATOR_ADDRESS	vazio	Endereço que pode congelar ativos
BRN_GENESIS_ALLOC	vazio	endereco:ativo:qtd,endereco:ativo:qtd
6. Execução do nó
6.1 Primeira execução
cmd
cd "C:\brn-rwa"
.venv\Scripts\activate
python node.py
6.2 Saída esperada
text
[identidade] nova: brn1f76771c09ef4291c…
[faucet] nova: brn16f12ea901c2e012f…
[chain] gênese RWA criada e persistida.
[node] P2P      : 0.0.0.0:7777
[node] rede     : brn-rwa-1
[node] DB       : blockchain.db
[node] altura   : 1
[node] wallet   : brn1f76771c09ef4291c……
[node] ativos   : ['BRN']
[web] painel RWA em http://0.0.0.0:5000 (user: admin)
 * Serving Flask app 'web_server'
 * Running on http://127.0.0.1:5000
 * Running on http://192.168.0.8:5000
Press CTRL+C to quit
6.3 Regra de ouro
⚠️ NUNCA FECHE essa janela. Ela é o servidor. Se fechar, tudo para.

Para parar:

Vá até a janela do CMD

Aperte Ctrl+C

Confirme com S (se perguntar)

6.4 Segunda execução
Na segunda vez, as carteiras e o DB já existem:

text
[identidade] carregada: brn1f76771c09ef4291c…
[faucet] carregada: brn16f12ea901c2e012f…
[chain] 2 blocos carregados de blockchain.db
6.5 Conectar outros nós (P2P)
Para adicionar um peer, edite .env:

text
BRN_SEED_PEERS=192.168.0.20:7777,192.168.0.30:7777
Cada nó faz broadcast UDP na porta 7777.

7. Interface web
7.1 Acesso
Abra o navegador em:

text
http://localhost:5000
Login:

Usuário: BRN_WEB_USER (default admin)

Senha: BRN_WEB_PASS (a que você definiu no .env)

7.2 O que o painel mostra
Cartões de status: Blocos, Ativos, KYC aprovados, Transferências, Slashed, Finalizado

Faucet: campo para pedir BRN de teste

Tabela de ativos: todos os ativos registrados com supply

Tabela de transferências: últimas 100 (on-chain + mempool)

Tabela de KYC: todos os endereços com status de compliance

7.3 Endpoints da API REST
Todas exigem HTTP Basic Auth:

Método	Endpoint	Descrição
GET	/	Dashboard HTML
GET	/api/summary	Snapshot completo
GET	/api/portfolio/<addr>	Saldos de um endereço
GET	/api/assets	Lista de ativos
GET	/api/slashing	Validadores banidos
GET	/api/finality	Status de finality
GET	/api/health	Health check (sem auth)
POST	/api/faucet	Pede BRN de teste
POST	/api/kyc	Registra/aprova KYC
7.4 Exemplos de uso da API
Portfólio:

cmd
curl -u admin:admin123 http://localhost:5000/api/portfolio/brn1abc...
Faucet:

cmd
curl -u admin:admin123 -X POST http://localhost:5000/api/faucet -H "Content-Type: application/json" -d "{\"address\":\"brn1abc...\"}"
Lista de ativos:

cmd
curl -u admin:admin123 http://localhost:5000/api/assets
8. Operações — guia prático
8.1 Criar uma carteira de usuário
CMD (segunda janela):

cmd
python -c "from cripto_wallet import WalletManager; import json; print(json.dumps(WalletManager.generate_keypair(), indent=2))"
Saída:

json
{
  "address": "brn1a1b2c3d4e5f6...",
  "spend_secret_key": "abcdef1234567890...",
  "public_key": "04abcdef..."
}
⚠️ Guarde as 3 chaves — sem a spend_secret_key você perde acesso aos fundos.

8.2 Pedir BRN do faucet
Abra http://localhost:5000

Cole o address no campo do faucet

Clique Pedir BRN

Aguarde ~10 segundos (tempo de bloco).

8.3 Consultar saldo
cmd
curl -u admin:admin123 http://localhost:5000/api/portfolio/brn1SEU_ENDERECO
8.4 Registrar um ativo novo
Pré-requisito: você precisa da chave privada de um endereço emissor.

Script Python (salve como criar_ativo.py):

python
from node import Node  # roda um nó temporário
import requests, json, os

# ATENÇÃO: em produção, use uma API separada. Aqui é para teste.
# Este script precisa que o node.py JÁ esteja rodando na mesma máquina.

API = "http://localhost:5000"
AUTH = ("admin", os.environ["BRN_WEB_PASS"])

# 1. Descobre o endereço do nó (emissor)
r = requests.get(f"{API}/api/summary", auth=AUTH)
assets = r.json()["assets"]
issuer_brn = next(a["issuer"] for a in assets if a["asset_id"] == "BRN")
print("Emissor BRN:", issuer_brn)
Na prática, o jeito mais simples de registrar um ativo é pelo código do próprio node.py. Abra um REPL:

cmd
python
E dentro dele:

python
import os
os.environ["BRN_MASTER_PASSWORD"] = "sua-senha-mestra-aqui"
from node import Node
from cripto_wallet import WalletManager
from bruno_blockchain_real import Blockchain, Transaction

# Carrega o nó (ele já está rodando em outro processo; aqui carregamos para pegar as chaves)
node = Node()  # ⚠️ conflito de porta — rode em outra máquina ou pare o nó antes
⚠️ Melhor prática: crie um script separado admin_cli.py que se conecta à API HTTP e assina transações localmente. Assim você não precisa reiniciar o nó.

Vou te mandar esse admin_cli.py em separado se você quiser. Por enquanto, o essencial é:

python
from cripto_wallet import WalletManager
import os

# Ler chave privada do emissor (você tem que ter guardado)
priv = "SUA_CHAVE_PRIVADA_AQUI"
pub  = WalletManager.generate_keypair()["public_key"]  # só exemplo
addr = WalletManager.address_from_public_key(pub)

# Assinar transação de criação de ativo
tx = Transaction.build(
    tx_type="asset_create",
    asset_id="PETR4",
    sender_address=addr,
    receiver_address=addr,
    amount=0,
    nonce=0,
    private_key_hex=priv,
    public_key_hex=pub,
    metadata={
        "name": "Petrobras PN Tokenizado",
        "symbol": "PETR4",
        "asset_type": "equity",
        "decimals": 2,
        "issuer": addr,
        "transfer_agent": addr,
        "max_supply": 1_000_000_000,
        "isin": "BRPETRACNPR6",
        "transfer_restricted": True,
    },
)
print(tx)  # Envie via POST /api/tx
8.5 Aprovar KYC de um endereço
API:

cmd
curl -u admin:admin123 -X POST http://localhost:5000/api/kyc -H "Content-Type: application/json" -d "{\"asset_id\":\"PETR4\",\"address\":\"brn1CLIENTE\",\"agent_address\":\"brn1EMISSOR\",\"agent_private_key\":\"...\",\"agent_public_key\":\"...\",\"nonce\":0,\"level\":\"accredited\",\"jurisdiction\":\"BR\"}"
8.6 Emitir ativo
python
from bruno_blockchain_real import Transaction
tx = Transaction.build(
    tx_type="issue", asset_id="PETR4",
    sender_address=issuer_addr, receiver_address=cliente_kyc,
    amount=1000, nonce=next_nonce,
    private_key_hex=issuer_priv, public_key_hex=issuer_pub,
)
8.7 Transferir ativo
python
tx = Transaction.build(
    tx_type="transfer", asset_id="PETR4",
    sender_address=cliente_a, receiver_address=cliente_b,
    amount=100, nonce=...,
    private_key_hex=..., public_key_hex=...,
)
Se qualquer um dos dois não tiver KYC aprovado, a transação é rejeitada.

8.8 Pagar dividendos
python
tx = node.bc.submit_dividend(
    asset_id="PETR4",
    issuer=issuer_addr,
    total_amount=5000.0,       # 5000 BRN distribuídos
    private_key=issuer_priv,
    public_key=issuer_pub,
    nonce=...,
)
Distribui proporcionalmente ao saldo de cada holder no momento do snapshot.

8.9 Congelar um ativo
python
tx = Transaction.build(
    tx_type="freeze", asset_id="PETR4",
    sender_address=issuer_addr, receiver_address=issuer_addr,
    amount=0, nonce=...,
    private_key_hex=..., public_key_hex=...,
    metadata={"freeze_asset": True},
)
Congela todas as transferências do ativo até unfreeze.

9. Segurança
9.1 Modelo de ameaças
Ameaça	Mitigação
Roubo de carteira	Argon2id (64 MiB, 3 iters) + AES-256-GCM
MITM no P2P	Apenas peers autorizados na rede (consórcio)
Acesso não autorizado ao painel	HTTP Basic Auth + rate limit
Exposição pública acidental	BRN_USE_NGROK=0 por default; aviso explícito
Roubo do .env	.gitignore + permissões 0600 em Linux
Double-spend	Nonce por endereço + verificação de UTXO
Validador malicioso	Slashing com evidência assinada
Reversão de blocos	Finality gadget (checkpoints)
KYC falso	Só issuer/transfer_agent aprova
Transferência para sanção	Jurisdição bloqueada por regra
9.2 Senhas
Senha	Onde	Regra
BRN_MASTER_PASSWORD	.env	≥ 20 caracteres, nunca versionar
BRN_WEB_PASS	.env	≥ 12 caracteres, trocar periodicamente
Senha das carteiras de usuário	Por usuário	≥ 12 caracteres (força via save_encrypted_wallet)
Nunca:

Comitar .env ou *.wallet no Git

Usar a mesma senha em produção e teste

Enviar chaves por e-mail/WhatsApp

Colar chaves privadas em chat (mesmo com IA)

9.3 Criptografia
Carteiras em repouso:

KDF: Argon2id (t=3, m=64 MiB, p=4)

Cifra: AES-256-GCM (AEAD autenticado)

Nonce: 12 bytes aleatórios

Salt: 16 bytes aleatórios

Formato: JSON com version, argon2, salt, nonce, ciphertext

Assinaturas:

Algoritmo: ECDSA secp256k1 + SHA-256 (determinístico, RFC 6979)

Domínio por transação (evita replay entre redes)

Blocos:

Hash: SHA3-256

Assinatura: ECDSA do validador

Cabeçalho inclui network_id (evita replay cross-network)

9.4 Rede
UDP P2P (7777):

Consórcio fechado — só peers autorizados

Não exponha à internet sem VPN (WireGuard/Tailscale)

Se quiser expor, use BRN_SEED_PEERS com hosts conhecidos

HTTP (5000):

Por default só escuta na LAN

Se expor publicamente, use HTTPS via reverse proxy (Caddy/nginx)

BRN_USE_NGROK=1 para túnel público — rotacione o token se vazar

9.5 Rate limiting
Endpoint	Limite
/api/summary	120/min
/api/portfolio	60/min
/api/faucet	3/hora por IP
/api/kyc	30/min
Global	300/hora por IP
9.6 Slashing
Validadores que:

Assinarem blocos inválidos

Tentarem reverter blocos finalizados

Produzirem duas versões do mesmo bloco

...são banidos permanentemente e perdem o stake, mediante evidência assinada por outro nó.

9.7 Checklist de segurança operacional
□ .env fora do Git (.gitignore cobre)
□ BRN_MASTER_PASSWORD ≥ 20 caracteres e única
□ BRN_WEB_PASS ≥ 12 caracteres e trocada a cada 90 dias
□ Backup de node_identity.wallet e faucet_identity.wallet em local seguro
□ P2P em rede privada (VPN se possível)
□ HTTPS habilitado se exposto publicamente
□ Monitoramento de /api/slashing e /api/finality
□ Logs revisados semanalmente
□ Usuário do processo não é root/admin
10. Backup e recuperação
10.1 O que fazer backup
Item	Frequência	Criticidade
node_identity.wallet	1x + cada geração	🔴 Crítico
faucet_identity.wallet	1x	🟡 Importante
blockchain.db	Diária	🟡 Importante
.env	Cada mudança	🔴 Crítico
mnemonic.txt (se usar HD)	1x	🔴 Crítico
10.2 Como fazer
cmd
mkdir backup
copy node_identity.wallet backup\
copy faucet_identity.wallet backup\
copy blockchain.db backup\
copy .env backup\
Guarde backup/ em:

HD externo

Pen drive em cofre

Cofre de senhas (chaves + senha mestra)

10.3 Recuperação
Para restaurar em outra máquina:

cmd
cd "C:\brn-rwa-novo"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy backup\node_identity.wallet .
copy backup\faucet_identity.wallet .
copy backup\blockchain.db .
copy backup\.env .

python node.py
Se o .env não tiver BRN_MASTER_PASSWORD original, as carteiras não abrem.

11. Troubleshooting
11.1 Erros de instalação
ModuleNotFoundError: No module named 'assets'
→ Falta o arquivo assets.py. Copie da pasta de origem.

ModuleNotFoundError: No module named 'dotenv'
→ pip install python-dotenv

ModuleNotFoundError: No module named 'flask'
→ pip install flask flask-limiter

pip: command not found
→ Python não está no PATH. Reinstale com "Add to PATH" marcado.

11.2 Erros de execução
[WinError 10048] endereço já em uso
→ Já existe um node.py rodando:

cmd
taskkill /F /IM python.exe
python node.py
[ERRO] BRN_MASTER_PASSWORD deve ter >= 20 caracteres
→ .env não lido ou senha curta. Confirme:

cmd
type .env
E verifique se está dentro da pasta do node.py.

Address already in use na porta 5000
→ Outro programa usa a 5000. Mude no .env:

text
BRN_WEB_PORT=5001
PermissionError: [WinError 5]
→ Rode o CMD como Administrador.

11.3 Erros de uso
Painel mostra "Login necessário"
→ Cache do navegador. Abra em aba anônima.

Faucet diz "Aguarde Xs para pedir novamente"
→ Cooldown ativo. BRN_FAUCET_COOLDOWN=3600 = 1 hora.

Transferência rejeitada com "remetente sem KYC válido"
→ O ativo tem transfer_restricted=True. Aprove KYC antes:

cmd
curl -u admin:admin123 -X POST http://localhost:5000/api/kyc -H "Content-Type: application/json" -d "{...}"
Saldo não aparece depois do faucet
→ Aguarde o bloco (10s) e recarregue o painel.

11.4 Como inspecionar o banco
Instale o DB Browser for SQLite, abra blockchain.db e veja:

blocks — cadeia completa

mempool — transações pendentes

slashing — validadores banidos

faucet_claims — histórico de faucet

registry — JSON com ativos + KYC

11.5 Logs
O Flask imprime cada request no CMD:

text
127.0.0.1 - - [12/Sep/2026 00:50:23] "GET /api/summary HTTP/1.1" 200 -
O nó imprime consenso, P2P e erros:

text
[consenso] bloco #3 (2 tx)
[p2p] novo peer: 192.168.0.20:54321
[slash] validador brn1abc… banido (bloco inválido #5)
Para salvar em arquivo:

cmd
python node.py > node.log 2>&1
12. Referência de arquivos
12.1 Estrutura
text
brn-rwa/
├── .env                    # Configuração (não versionar)
├── .env.example            # Template
├── .gitignore              # Exclusões do Git
├── blockchain.db           # SQLite (gerado)
├── node_identity.wallet    # Carteira do nó (gerado)
├── faucet_identity.wallet  # Carteira do faucet (gerado)
├── bruno_blockchain_real.py
├── assets.py
├── node.py
├── web_server.py
├── cripto_wallet.py
├── index.html
├── app_wallet.py           # GUI opcional
├── requirements.txt
└── tests/                  # Testes automatizados
12.2 Carteiras do nó
node_identity.wallet — identidade do validador

Endereço: brn1f76771c09ef4291c...

Assina blocos

Recebe recompensa por bloco (BRN)

faucet_identity.wallet — distribuidor de BRN de teste

Endereço: brn16f12ea901c2e012f...

Recebe alocação de gênese (1.000.000 BRN por padrão)

Envia 100 BRN por claim

12.3 Formato do arquivo .wallet
json
{
  "version": 2,
  "method": "aes-256-gcm-argon2id",
  "argon2": {
    "time_cost": 3,
    "memory_cost": 65536,
    "parallelism": 4,
    "hash_len": 32
  },
  "salt": "base64...",
  "nonce": "base64...",
  "ciphertext": "base64..."
}
Decifrar requer: senha + algoritmo Argon2id idêntico ao salvo.

13. Compliance e auditoria
13.1 Trilha de auditoria
Toda transação fica registrada no bloco com:

type — tipo de operação

asset_id — ativo

from, to — endereços

amount — quantidade

timestamp — data/hora

signature — assinatura ECDSA

metadata — dados específicos (ISIN, jurisdição, etc.)

13.2 Exportar para auditoria
API:

cmd
curl -u admin:admin123 "http://localhost:5000/api/summary" > auditoria.json
SQL direto:

sql
-- Todas as transferências de PETR4
SELECT idx, timestamp, data FROM blocks
WHERE data LIKE '%PETR4%'
ORDER BY idx DESC;
13.3 Relatórios esperados
Relatório	Periodicidade	Origem
Transferências confirmadas	Diária	blocks
KYC aprovados	Semanal	registry
Congelamentos	Mensal	blocks
Slashing	Sob demanda	slashing
Finality status	Diária	finality
13.4 Regulador
Se BRN_REGULATOR_ADDRESS estiver definido, esse endereço pode:

Congelar qualquer ativo

Aprovar/revogar KYC

Audit completo via API

14. Produção
14.1 Antes de ir para produção
□ Substituir Flask dev server por gunicorn (Linux) ou waitress (Windows)
□ HTTPS obrigatório via reverse proxy (Caddy/nginx)
□ BRN_USE_NGROK=0 (usar proxy próprio)
□ Firewall: UDP 7777 só entre IPs do consórcio
□ Backup automático diário do blockchain.db
□ Monitoramento (Prometheus/Grafana)
□ Logs centralizados (Loki/ELK)
□ Processo supervisionado (systemd/NSSM)
□ Autenticação multifator no painel (fora do escopo atual)
14.2 Escalar para 3+ validadores
Cada validador em uma máquina:

text
Máquina 1: 192.168.0.10:7777  →  BRN_SEED_PEERS=192.168.0.11:7777,192.168.0.12:7777
Máquina 2: 192.168.0.11:7777  →  BRN_SEED_PEERS=192.168.0.10:7777,192.168.0.12:7777
Máquina 3: 192.168.0.12:7777  →  BRN_SEED_PEERS=192.168.0.10:7777,192.168.0.11:7777
O consenso é probabilístico por stake; finality requer 67% do stake votando.

14.3 Serviço Windows (NSSM)
cmd
nssm install BRN-RWA "C:\brn-rwa\.venv\Scripts\python.exe" "C:\brn-rwa\node.py"
nssm set BRN-RWA AppDirectory "C:\brn-rwa"
nssm set BRN-RWA AppStdout "C:\brn-rwa\logs\node.log"
nssm set BRN-RWA AppStderr "C:\brn-rwa\logs\node.err"
nssm start BRN-RWA
14.4 Systemd (Linux)
ini
# /etc/systemd/system/brn-rwa.service
[Unit]
Description=BRN RWA Node
After=network.target

[Service]
Type=simple
User=brn
WorkingDirectory=/opt/brn-rwa
ExecStart=/opt/brn-rwa/.venv/bin/python node.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
bash
sudo systemctl daemon-reload
sudo systemctl enable --now brn-rwa
14.5 Gunicorn (Linux)
bash
gunicorn -w 4 -b 0.0.0.0:5000 web_server:app
Ajuste em web_server.py para não iniciar em modo dev quando detectar produção.

📎 Anexos
A. Comandos mais usados
cmd
:: Ativar ambiente
.venv\Scripts\activate

:: Rodar nó
python node.py

:: Parar (Ctrl+C na janela do nó)

:: Matar todos os Python
taskkill /F /IM python.exe

:: Criar carteira
python -c "from cripto_wallet import WalletManager; import json; print(json.dumps(WalletManager.generate_keypair(), indent=2))"

:: Ver portfólio
curl -u admin:admin123 http://localhost:5000/api/portfolio/brn1...

:: Ver resumo
curl -u admin:admin123 http://localhost:5000/api/summary

:: Ver ativos
curl -u admin:admin123 http://localhost:5000/api/assets

:: Pedir BRN do faucet
curl -u admin:admin123 -X POST http://localhost:5000/api/faucet -H "Content-Type: application/json" -d "{\"address\":\"brn1...\"}"
B. Glossário
Termo	Significado
Ativo	Token que representa algo do mundo real (ação, imóvel, ouro)
Issuer	Endereço que emite/resgata um ativo
Transfer Agent	Endereço que aprova KYC
KYC	Know Your Customer — verificação de identidade
AML	Anti-Money Laundering — prevenção à lavagem
RWA	Real World Asset — ativo do mundo real tokenizado
Snapshot	Cópia dos saldos no momento de um evento
Finality	Garantia de que um bloco não será revertido
Slashing	Perda de stake por má conduta
Mempool	Fila de transações aguardando inclusão em bloco
Genesis	Primeiro bloco da cadeia
C. Contatos e suporte
Para dúvidas técnicas:

Consulte README.md

Verifique tests/ para exemplos

Consulte logs em node.log

🏁 Fim do manual
🧪 Como testar (dois nós, mesma máquina)

Terminal A — nó 1

```bash
export NGROK_AUTHTOKEN="SEU_TOKEN_NOVO"
export BRN_P2P_PORT=7777
export BRN_WEB_PORT=5000
export BRN_DB_PATH=node1.db
export BRN_IDENTITY_FILE=node1_identity.json
python node.py
```

Terminal B — nó 2 (aponta para o nó 1)

```bash
export BRN_P2P_PORT=7778
export BRN_WEB_PORT=5001
export BRN_DB_PATH=node2.db
export BRN_IDENTITY_FILE=node2_identity.json
export BRN_SEED_PEERS=127.0.0.1:7777
python node.py
```

O que observar

· No nó 1, o loop de consenso assina o bloco com a chave de node1_identity.json.
· No nó 2, ao receber a mensagem chain, o replace_chain valida cada bloco:
  · hash == calculate_hash()
  · validator == address_from_public_key(validator_public_key)
  · verify_signature(pubkey, {"block_hash", "index"}, signature)

# 1) Instalar dependências
pip install -r requirements.txt

# 2) Testar carteira
python main.py

# 3) Subir nó (em outra máquina ou terminal)
BRN_P2P_PORT=7777 python node.py
✅ O que você precisa fazer
Você ainda não baixou o projeto. Precisa clonar primeiro. Siga exatamente estes comandos, um de cada vez:

1. Volte uma pasta (para não clonar dentro de brn)
No terminal, digite:

cmd
cd ..
Agora você estará em C:\Users\mayra\OneDrive\Imagens\bruno crypto\.

2. Clone o repositório
cmd
git clone https://github.com/brunoldo2312/brnfinal.git
Isso vai criar uma nova pasta chamada brnfinal com todos os arquivos do projeto. Se der erro dizendo que git não é reconhecido, você precisa instalar o Git primeiro: https://git-scm.com/download/win

3. Entre na pasta clonada
cmd
cd brnfinal
4. Veja o que tem dentro
cmd
dir
Agora sim você deve ver os arquivos do projeto: package.json, iniciar.bat, README.md, etc. Só a partir daqui os comandos npm install e iniciar.bat vão funcionar.

5. A partir daqui, siga um destes caminhos:
Se existir iniciar.bat (o arquivo que você mencionou):

cmd
iniciar.bat
Se existir package.json (projeto Node.js):

cmd
npm install
npm start
⚠️ Um alerta importante sobre o OneDrive
Sua pasta está dentro do OneDrive (C:\Users\mayra\OneDrive\...). Isso costuma causar problemas com projetos de programação, porque:

O OneDrive fica sincronizando arquivos enquanto o npm/node tenta ler e escrever

Isso pode causar erros estranhos, lentidão e arquivos "travados"

Recomendação: clone o projeto em uma pasta fora do OneDrive, por exemplo:

cmd
cd C:\
mkdir projetos
cd projetos
git clone https://github.com/brunoldo2312/brnfinal.git
📋 Resumo do que aconteceu
O que você fez	O que deveria ter feito
Colou a URL no terminal	Rodar git clone <URL>
Rodou npm install numa pasta vazia	Clonar o projeto primeiro, depois entrar na pasta
Tentou rodar iniciar.bat que não existe	Primeiro clonar, depois conferir se o arquivo existe
Rode o passo 2 (git clone ...) e me diga o que apareceu. Se der certo, rode dir dentro da pasta brnfinal e me mande a lista de arquivos — assim eu te digo exatamente qual comando usar para iniciar.

Agora sim, deu certo! 🎉 O projeto foi clonado com sucesso e vejo que você já tem os arquivos dentro da pasta brnfinal. E olha só: é um projeto em Python, não em Node.js. Isso explica por que o npm install não funcionou antes — não tinha nada a ver com Node.

📋 O que você tem aí
Olhando a lista de arquivos, dá para identificar:

Arquivo	O que é
iniciar_brn.bat	✅ Provavelmente é o seu "iniciar tudo de uma vez"
iniciar_brn.cmd	Outra versão do inicializador
instalar_ngrok.bat	Instalador do ngrok (usado para expor serviços localmente)
main.py, explorer.py, crypto_wallet.py	Código Python do projeto
blockchain_node_6001.db	Banco de dados do nó da blockchain
index.html	Página web do projeto
README.md	Documentação
LICENSE	Licença
✅ Como executar
Como você está na pasta certa (brnfinal), agora basta rodar:

cmd
iniciar_brn.bat
explorador de blocos  no link https://seventy-rigging-ploy.ngrok-free.dev
1. Localmente (na sua própria máquina)
Abra o navegador em:

text
http://localhost:8080
ou

text
http://127.0.0.1:8080
Importante: o explorer.py é um processo separado. O main.py sobe o nó BRN (porta 6001) e o túnel Ngrok (porta 8080), mas não sobe o explorer. Você precisa rodar o explorer em outra janela do CMD, com o venv ativo:

cmd
cd "C:\Users\adnac\Desktop\bruno crypto\agora-brn"
env\Scripts\activate
python explorer.py
Saída esperada:

text
* Running on http://127.0.0.1:8080
Deixe essa janela aberta. Em outra janela, rode o main.py 6001. Aí o explorador aparece no navegador.

Se o explorer.py não estiver na pasta, é porque não foi baixado. Verifique com:

cmd
dir explorer.py
Se não achar, baixe do repositório original:

cmd
curl -L -o explorer.py https://raw.githubusercontent.com/brunoldo2312/brnfinal/main/explorer.py
(se o caminho não funcionar, abra o repositório no GitHub, clique em explorer.py → Raw → salve com Ctrl+S em explorer.py.)

2. Pelo painel do Ngrok (mostra a URL pública)
Enquanto o main.py estiver rodando, abra:

text
http://127.0.0.1:4040
Esse é o painel local do Ngrok. Ele mostra uma URL do tipo:

text
https://abcd-1234.ngrok-free.app

🖥️ Opção 1: Prompt de Comando (CMD)

No CMD, a sintaxe VAR=valor do Linux não funciona. Usamos set e && para encadear os comandos. Copie e cole:

```cmd
set "NGROK_AUTHTOKEN=3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4" && python main.py
```

⚡ Opção 2: PowerShell

Se você usa o PowerShell, a sintaxe é um pouco diferente:

```powershell
$env:NGROK_AUTHTOKEN="3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4"; python main.py
```

---

📄 Opção 3: Criar um Script .cmd (Recomendado)

Se você quiser dar apenas um duplo clique para rodar tudo, crie um arquivo chamado iniciar_brn.cmd na mesma pasta do seu main.py e cole o código abaixo. Ele configura o token e inicia o programa automaticamente:

```cmd
@echo off
title Iniciar Moeda Bruno (BRN) com Ngrok
cd /d "%~dp0"

echo ========================================================
echo       CONFIGURANDO TOKEN DO NGROK E INICIANDO O NO
echo ========================================================
echo.

:: Token configurado abaixo
set "NGROK_AUTHTOKEN=3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4"

echo Token configurado. Iniciando main.py...
echo.

python main.py

echo.
echo O programa foi encerrado.
pause
```

📌 Observações Importantes:

1. O main.py inicia o Ngrok sozinho? Se o seu main.py já estiver programado para ler a variável NGROK_AUTHTOKEN e criar o túnel automaticamente, os comandos acima funcionarão perfeitamente.
2. Se o main.py não iniciar o Ngrok: Você precisará abrir outro terminal e rodar ngrok http 8080 manualmente, como fizemos nos passos anteriores.
3. Substitua o token: Novamente, use o token apenas para testar e depois troque-o no painel do Ngrok por um novo, mantendo este em segredo.

🚀 Passo 1: Criar uma Conta no Ngrok

1. Acesse https://dashboard.ngrok.com/signup e crie uma conta gratuita.
2. Após o login, o painel exibirá o seu Authtoken (uma sequência longa de letras e números). Copie-o, pois você precisará dele no próximo passo.

💾 Passo 2: Instalar o Ngrok no Linux (Ubuntu/Debian)

No terminal do seu computador, execute os comandos abaixo para instalar via apt (a forma mais simples):

```bash
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | \
  sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null && \
  echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | \
  sudo tee /etc/apt/sources.list.d/ngrok.list && \
  sudo apt update && \
  sudo apt install ngrok
```

Caso prefira baixar o binário diretamente:

```bash
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar xvzf ngrok-v3-stable-linux-amd64.tgz
sudo mv ngrok /usr/local/bin/
```

Após a instalação, verifique se funcionou com ngrok version.

🔑 Passo 3: Conectar o Ngrok à Sua Conta

Execute o comando abaixo, substituindo SEU_TOKEN_AQUI pelo Authtoken que você copiou no Passo 1:

```bash
ngrok config add-authtoken SEU_TOKEN_AQUI
```

Isso vincula o Ngrok instalado na sua máquina à sua conta.

⛓️ Passo 4: Iniciar o Explorador de Blocos

Em um terminal, navegue até a pasta do projeto e execute o explorador (criado anteriormente) na porta 8080:

```bash
python3 explorer.py
```

Mantenha este terminal aberto. O explorador precisa estar rodando para que o Ngrok consiga redirecionar o tráfego para ele.

🌐 Passo 5: Criar o Túnel Público com Ngrok

Abra outro terminal e execute:

```bash
ngrok http 8080
```

Você verá uma saída semelhante a esta (o link é um exemplo):

```
Forwarding    https://abcd-1234.ngrok-free.app -> http://localhost:8080
```

O endereço https://abcd-1234.ngrok-free.app é o seu link público temporário. Qualquer pessoa que acessá-lo verá o seu explorador de blocos.

📋 Passo 6: Preencher o Formulário da Exchange

No formulário da cexswap.cc, no campo "Explorador de blocos", cole o link que o Ngrok gerou (ex: https://abcd-1234.ngrok-free.app). O link deve começar com https://.

---

⚠️ Avisos Importantes sobre a "Opção Rápida"

· Temporário: O link gerado pelo Ngrok é efêmero. Ele expira assim que você fecha o terminal do Ngrok ou desliga o computador. Se a exchange fizer uma verificação depois, o link estará quebrado.
· Limitações do Plano Gratuito: Contas gratuitas do Ngrok têm restrições de tempo de sessão e número de conexões simultâneas. Para um uso mais estável, seria necessário um plano pago.
· Segurança: Expor seu computador local à internet traz riscos. O Ngrok cria um túnel, mas não substitui a segurança de um servidor dedicado. Certifique-se de que seu explorador não exponha dados sensíveis.
· Não é uma Solução Definitiva: Para que a exchange aceite e mantenha a listagem, o ideal é hospedar o explorador em uma VPS (Servidor Virtual Privado) com um domínio próprio. O Ngrok é excelente para testes rápidos, mas não é indicado para produção.

Resumo: Use o Ngrok para obter o link rapidamente e preencher o formulário, mas esteja ciente de que a exchange pode rejeitar a solicitação por causa da natureza temporária do link. O próximo passo recomendado é migrar o explorador para uma hospedagem permanente.
# 💼 Moeda Bruno (BRN) - Carteira Avançada & Blockchain P2P

A **Moeda Bruno (BRN)** é uma implementação experimental de um ecossistema de criptomoeda descentralizado baseado em princípios acadêmicos do protocolo *CryptoNote/Monero*. O projeto apresenta uma arquitetura modular com um livro-razão imutável, sincronização autônoma de nós Peer-to-Peer (P2P), propagação de transações via Mempool Broadcast e um utilitário automático de redirecionamento de portas (UPnP).

---

## 🚀 Funcionalidades

*   **Consenso de Maior Cadeia (*Longest Chain Rule*):** Algoritmo de resolução de consenso que substitui atomicamente a cadeia local se um par remoto apresentar uma blockchain estritamente mais longa e válida.
*   **Gênese Determinístico (Cadeia Única):** O bloco 0 tem timestamp, nonce e hash fixos (`GENESIS_HASH`), então **todo computador cria exatamente a mesma gênese** e todos compartilham **uma única blockchain**. Nós com gênese diferente (cadeias antigas/divergentes) são recusados na sincronização.
*   **Mempool Persistente com Gossip:** Transações pendentes são gravadas em disco (`mempool_node_<porta>.json`), sobrevivem a reinicializações, são retransmitidas aos demais pares (relay) e removidas automaticamente quando confirmadas em bloco ou após sincronização de cadeia. Transações recebidas com remetente ainda sem saldo local (nó atrasado) vão para uma fila de órfãs (`*.orphan.json`) e são promovidas assim que a cadeia se atualiza.
*   **Livro-Razão Relacional (SQLite3):** Persistência imutável indexada com auditoria histórica e reconstituição dinâmica de saldos em tempo real.
*   **Backup Criptografado de Chaves (.wallet):** Cifragem simétrica em fluxo utilizando derivação de chaves PBKDF de 5000 rounds para proteção de Spend Keys locais.
*   **Interface Gráfica Nativa (Desktop Puro):** Janela escura desacoplada construída sobre a ponte de injeção JavaScript-Python (`pywebview` + `PyQt6`).
*   **Endereço de Recebimento:** Botão para copiar o endereço público da carteira com um clique, facilitando o recebimento de BRN sem expor a chave privada.

---

## 📁 Estrutura Modular do Projeto

O ecossistema foi dividido em módulos isolados para garantir a consistência de dados, mitigar erros de tokenização e otimizar o tempo de compilação:

```text
├── bruno_blockchain_real.py  # Motor principal, API de controle, mempool e chamadas P2P
├── cripto_db.py              # Camada de persistência relacional e queries ordinais SQLite3
├── cripto_wallet.py          # Gerenciador de backup e criptografia PBKDF simétrica
├── index.html                # Interface visual baseada na ponte Javascript-Python Native
├── blockchain_node_<porta>.db   # Cadeia de blocos do nó (SQLite)
├── mempool_node_<porta>.json    # Transações pendentes do nó (persistidas em disco)
└── wallets/                     # Backups .wallet criptografados
```

### Receber BRN

1. Crie ou importe uma carteira.
2. Abaixo de **Seu Endereço Público**, clique em **Copiar endereço de recebimento**.
3. Envie somente esse endereço `brn1...` para quem fará o depósito. Nunca compartilhe a chave privada.

## Segurança e limites do protótipo

Esta é uma blockchain educacional, não indicada para valores reais. A versão atual valida a assinatura contra o endereço do remetente, impede gasto duplo na mempool, rejeita blocos/cadeias com gastos sem saldo e limita mensagens P2P recebidas.

Backups novos usam `cryptography` (Fernet com PBKDF2-SHA256 e 600.000 iterações), exigem senha de ao menos 12 caracteres e são gravados na pasta `wallets/`. Backups antigos Fernet ainda podem ser importados e devem ser reexportados. Instale as dependências antes de iniciar:

```bash
pip install ecdsa cryptography pywebview pyqt6
```

O UPnP deixou de ser ativado automaticamente. Só exponha a porta da carteira à internet se você entender e aceitar esse risco; para testes na mesma rede, use a sincronização manual da interface.

---

## 🛠️ Como Executar o Projeto (Máquina Local)

### 1. Preparação do Ambiente e Dependências (Linux Ubuntu)
Abra o terminal no diretório do projeto e execute os comandos para instalar as bibliotecas de sistema e isolar o ambiente virtual:

```bash
# Instalar pacotes de sistema necessários
sudo apt update && sudo apt install python3-venv python3-full python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 -y

# Criar e ativar o ambiente virtual (VENV)
python3 -m venv env
source env/bin/activate

# Instalar dependências de execução e o motor gráfico isolado PyQt6
pip install --upgrade pip
pip install pywebview flask pyqt6 PyQt6-WebEngine qtpy
```

### 2. Inicialização do Nó Principal
Sempre limpe os bancos de dados corrompidos ou inconsistentes de sessões anteriores antes de iniciar o nó na porta de sua escolha (Ex: `6001`):

```bash
rm -rf __pycache__
rm -f *.db mempool_node_*.json
python3 bruno_blockchain_real.py 6001
```

---

## 🌐 Sincronização entre Máquinas Físicas Diferentes

Para rodar a Moeda Bruno em múltiplos computadores conectados na mesma rede Wi-Fi ou cabo:

### 1. Identificar o IP da Máquina Principal
No terminal do seu nó principal (Ex: Seu HP Pavilion), execute:
```bash
hostname -I
# Retornará algo como: 192.168.0.17
```

### 2. Executar o Nó na Segunda Máquina

> **IMPORTANTE — cadeia única:** antes de iniciar, apague os bancos antigos/divergentes em **todos** os computadores (`blockchain_node_*.db`, `mempool_node_*.json`). Cadeias criadas por versões anteriores têm gênese com hash diferente e **não sincronizam** (o nó avisa "gênese pertence a outra rede"). Com a gênese determinística, cada nó recria o mesmo bloco 0 e todos passam a compartilhar **uma única blockchain**.

Copie os arquivos do projeto para o segundo computador. Abra o terminal dele e inicie o script alterando a porta de escuta para não gerar conflitos:

*   **No Linux:** `python3 bruno_blockchain_real.py 6002`
*   **No Windows (CMD Administrador):** 
    ```cmd
    python -m venv env
    .\env\Scripts\activate
    pip install pywebview flask pyqt6 PyQt6-WebEngine qtpy
    python bruno_blockchain_real.py 6002
    ```

### 3. Sincronizar as Cadeias de Blocos
1. Vá até a tela do aplicativo na **Segunda Máquina**.
2. No painel superior rosa (**Rede Descentralizada**), insira o IP do seu nó principal: `192.168.0.17`.
3. Defina a porta remota do nó principal: `6001`.
4. Clique em **"Conectar e Sincronizar Cadeira"**. O ecossistema fará o download e a verificação criptográfica do livro-razão automaticamente.

*Nota de Firewall:* A conexão P2P precisa de **porta de entrada liberada nos dois computadores**.
- **Windows (CMD Administrador):** `netsh advfirewall firewall add rule name="Bruno BRN 6001" dir=in action=allow protocol=TCP localport=6001` (troque 6001 pela porta do nó; faça em cada máquina para a sua porta).
- **Linux:** `sudo ufw allow 6001/tcp`.

### 4. Os dois computadores DEVEM compartilhar a mesma blockchain

Se um micro "envia mas o outro não recebe", quase sempre é porque estão em cadeias diferentes. Checklist:

1. **Código atualizado nos dois:** copie `bruno_blockchain_real.py`, `cripto_db.py`, `cripto_wallet.py` e `index.html` atualizados para o outro computador. Nó com código antigo cria gênese divergente.
2. **Convergência automática:** ao iniciar com o código novo, se o banco local tiver gênese antiga/divergente o nó faz backup (`*.divergent-*.bak`) e recria a gênese padrão `0000d904…`. Não é preciso apagar nada manualmente.
3. **Confirme na tela:** o card "🌐 Rede" mostra `🟢 cadeia única compartilhada` com a gênese. Se aparecer `🔴 cadeia divergente`, reinicie o nó. Os dois computadores devem mostrar o **mesmo** hash de gênese.
4. **Firewall nos dois sentidos:** cada máquina deve aceitar entrada na própria porta (veja acima).
5. **Mesma rede:** os dois IPs devem se enxergar (mesmo Wi-Fi/cabo). Teste com `ping <IP-do-outro>`.
6. **Sincronize:** em um dos nós, informe o IP/porta do outro e clique em "Conectar e Sincronizar". A cadeia maior vence e ambos ficam idênticos; transações pendentes se propagam via mempool/gossip.

---

## 📦 Como Gerar o Executável Binário (.App / .Exe)

Para distribuir a carteira de privacidade como um aplicativo desktop nativo e independente (sem a necessidade de instalação prévia do Python na máquina de destino):

### No Linux (Gera binário executável nativo)
```bash
pip install pyinstaller
pyinstaller --onefile --add-data "index.html:." --windowed bruno_blockchain_real.py

# Para executar o binário gerado na pasta dist/
cd dist
chmod +x bruno_blockchain_real
./bruno_blockchain_real 6001
```

### No Windows (Gera o arquivo executável .exe)
Abra o Prompt de Comando (CMD) do Windows dentro da pasta do projeto e execute:
```cmd
pip install pyinstaller
pyinstaller --onefile --add-data "index.html;." --windowed bruno_blockchain_real.py
```
O arquivo unificado estará disponível no diretório `dist/bruno_blockchain_real.exe`.
