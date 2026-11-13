# Respostas Instagram para Prefeituras

Sistema que responde automaticamente comentários no Instagram de **várias prefeituras**, com um painel web para cada equipe cuidar das próprias respostas.

**Princípios pensados para o setor público**

- As respostas são **textos aprovados** pela prefeitura. Nada de texto inventado.
- Críticas, denúncias, assuntos sensíveis, dados pessoais (CPF, telefone, e-mail) e dúvidas sem resposta aprovada **não recebem resposta automática**: vão para uma **fila da equipe**, que responde pelo painel.
- Limite de respostas por hora, espera aleatória antes de publicar e variações de texto, para não parecer spam.
- Cada prefeitura vê apenas os próprios dados. Tokens do Instagram ficam criptografados no banco.
- Comentários são apagados automaticamente após um prazo (padrão: 365 dias).

## Como funciona

```
Instagram ──(aviso de novo comentário)──▶ /webhook ──▶ regras da prefeitura ──▶ resposta pública
                                                    └─▶ assunto sensível / sem regra ──▶ fila no painel
```

## O que você precisa

1. **Conta de desenvolvedor da Meta** e um app criado em [developers.facebook.com](https://developers.facebook.com).
2. **Instagram profissional** (Comercial ou Criador de Conteúdo) em cada prefeitura.
3. **Hospedagem com HTTPS** e disco persistente (veja "Colocar no ar").
4. **Docker** (ou Python 3.11+).
5. Os valores secretos do arquivo `.env` (passo 2 abaixo).

## 1. Rodar no seu computador (teste)

```bash
cp .env.example .env
python3 -m app.keys          # imprime SECRET_KEY, ENCRYPTION_KEY e META_VERIFY_TOKEN: cole no .env
# preencha também ADMIN_EMAIL, ADMIN_PASSWORD e META_APP_SECRET; use COOKIE_SECURE=0 e PROXY_FIX=0 no teste local
docker compose up --build
```

Abra http://localhost:8000 e entre com o e-mail e a senha do administrador.

Sem Docker: `pip install -r requirements.txt`, exporte as variáveis do `.env` e rode `gunicorn --workers 1 --threads 8 wsgi:app`.

> Use sempre **um único processo** (`--workers 1`). As respostas agendadas ficam na memória do processo. Se o servidor reiniciar no meio, o que estava pendente volta para a fila da equipe.

## 2. Configurar o app na Meta

A interface da Meta muda com frequência. Se algum nome abaixo estiver diferente, siga a [documentação oficial do Instagram Platform](https://developers.facebook.com/docs/instagram-platform).

1. Crie um app do tipo **Business** e adicione o produto **Instagram** (opção *API setup with Instagram login*).
2. Em **Configurações do app > Básico**, copie o **App Secret** para `META_APP_SECRET`.
3. Permissões necessárias: `instagram_business_basic` e `instagram_business_manage_comments`.
4. Adicione a conta Instagram profissional da prefeitura ao app e **gere o token de acesso** (longo, de 60 dias). Cole esse token no painel (passo 4). O sistema renova sozinho a cada ~20 dias.
5. Em **Webhooks** do produto Instagram:
   - **URL de retorno de chamada:** `https://SEU-ENDERECO/webhook`
   - **Token de verificação:** o mesmo valor de `META_VERIFY_TOKEN`
   - Assine o campo **`comments`** e ative o recebimento de webhooks para a conta.
6. O **ID numérico da conta** do Instagram aparece nesse mesmo painel. Se você não achar, comente em um post da conta: o log do servidor mostrará "Webhook de conta desconhecida: NÚMERO".

**Acesso a contas de terceiros.** Enquanto o app estiver em modo de desenvolvimento (Standard Access), ele funciona apenas com contas que você possui ou administra e adicionou ao app. Para operar contas de **outras** prefeituras com tranquilidade, a Meta exige **Advanced Access**: verificação da empresa e revisão do app, com política de privacidade pública e vídeo mostrando o uso. Comece esse processo cedo, pois é a etapa mais demorada.

## 3. Colocar no ar

Qualquer hospedagem que rode Docker serve (Railway, Render, Fly.io, VPS com Docker). O que não pode faltar:

- **HTTPS** (as plataformas já entregam).
- **Disco persistente montado em `/data`**. O banco SQLite fica lá. Sem isso, os dados somem a cada novo deploy. Confira se o seu plano oferece volume/disco persistente.
- As variáveis do `.env` configuradas no painel da hospedagem (nunca dentro do código).
- `COOKIE_SECURE=1` e `PROXY_FIX=1` (já são o padrão).
- Se a hospedagem exigir uma porta, ela é lida da variável `PORT`.

**Faça backup** do arquivo `/data/respostas.db` periodicamente e guarde a `ENCRYPTION_KEY` em local seguro: sem ela, os tokens salvos não podem ser lidos.

## 4. Usar o painel

1. **Cadastrar prefeitura** (administrador): nome, @ do Instagram, ID numérico da conta e token.
2. **Respostas:** crie regras com palavras-chave e uma ou mais variações de resposta (uma por linha).
   - `vacin*` pega vacina, vacinação, vacinar...
   - `{usuario}` vira o @ de quem comentou; `{contato}` vira o contato oficial da prefeitura.
3. **Testar um comentário** (na aba Respostas): mostra o que o sistema faria, sem enviar nada.
4. **Fila da equipe:** responda ou marque como tratado. A resposta sai no Instagram em nome da prefeitura.
5. **Usuários:** o administrador cria logins da "equipe da prefeitura", que enxergam só a própria prefeitura.
6. **Configurações:** pausar a prefeitura, limite por hora, assuntos extras que sempre vão para a equipe e IA opcional.

## O que o sistema nunca responde sozinho

Comentário com denúncia, corrupção, ameaça, urgência/emergência, violência, suicídio, termos jurídicos e outros (lista completa na aba Configurações), além de CPF/telefone/e-mail. Também ignora: comentários só com emoji, com link (spam), respostas dentro de conversas e os da própria conta.

## IA opcional

Se `ANTHROPIC_API_KEY` estiver configurada e a opção ligada na prefeitura, quando nenhuma palavra-chave casar a IA **escolhe uma resposta já cadastrada** (ou nenhuma). Ela não escreve texto livre, e qualquer falha manda o comentário para a fila. O texto do comentário é tratado como dado não confiável.

## Comandos úteis

```bash
python -m app.keys                         # gera os valores secretos
flask --app app create-admin EMAIL         # cria ou troca a senha de um administrador
flask --app app refresh-tokens             # renova agora os tokens que estão para vencer
pip install -r requirements-dev.txt && python -m pytest   # roda os testes
```

## Limitações conhecidas

- **Testado com Instagram simulado.** Os 49 testes automáticos e um servidor falso da API conferem o formato das chamadas, mas este projeto **não foi testado contra o Instagram real**. Antes de usar com prefeituras, teste com uma conta sua e confira na documentação da Meta se endpoints, permissões e nome do app ainda batem.
- SQLite e um único processo atendem bem dezenas de contas. Para volume muito maior, o próximo passo é PostgreSQL e uma fila de tarefas.
- Responde apenas comentários principais em posts e Reels. Não responde lives, não esconde comentários ofensivos e não envia mensagem direta.
- O painel não tem autenticação em dois fatores. Se necessário, coloque-o atrás do login da hospedagem ou de uma VPN.
- Cumprir a LGPD também depende de processos da prefeitura (base legal, política de privacidade, contrato de operador). O sistema ajuda com: dados mínimos, apagamento automático, separação por prefeitura e tokens criptografados.
