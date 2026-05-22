# Bot de XP para Tibia

Bot de Discord para monitorar a XP diaria de uma PT usando dados do highscore do RubinOT.

Fluxo pratico:

- voce informa a XP total da PT
- o bot busca o ranking `Daily Experience (raw)`
- ele diz em qual posicao sua PT ficaria no rank
- tambem mostra se voces passaram o top 1/top 5

Fluxo opcional por personagem:

- voce cadastra os 4 personagens uma vez
- a primeira leitura do dia vira a XP inicial
- cada atualizacao consulta o highscore do RubinOT
- o bot mostra level, XP inicial, XP atual, XP feita e XP justa
- player `x5` e comparado como `XP feita / 5`

## Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite o `.env` e coloque o token:

```env
DISCORD_TOKEN=seu_token_aqui
RUBINOT_BASE_URL=https://rubinot.com.br
RUBINOT_DAILY_CATEGORY=7
RUBINOT_HIGHSCORE_CATEGORY=experience
RUBINOT_CF_CLEARANCE=cole_o_cf_clearance_aqui
```

## Rodar

```powershell
python xp_bot.py
```

## Comparar PT contra o ranking diario

Ver o top diario usado como referencia:

```text
/top_diario
/top_diario mundo:Cellenium
```

Comparar a XP total da PT:

```text
/comparar_pt xp:850kk
/comparar_pt xp:1.4bi
/comparar_pt xp:850kk mundo:Cellenium
```

Se a categoria `Daily Experience (raw)` tiver outro ID no RubinOT, configure:

```text
/categoria_diaria categoria:7
```

Tambem da para testar direto:

```text
/top_diario categoria:7
/comparar_pt xp:850kk categoria:7
```

## Painel web local

O painel local mostra:

- maior XP do ranking carregado
- maior e menor ganho desde a ultima leitura
- PTs destacadas e comparadas entre si
- tabela do rank do mundo
- cadastro de PTs pelo proprio site, com 4 ou 5 integrantes
- analise se a PT foi excelente, boa ou ruim conforme meta de XP

Cadastre sua PT e as PTs que quer comparar pelo site. Primeiro configure uma conta admin no `.env`:

```env
RANKZADA_ADMIN_USER=admin
RANKZADA_ADMIN_PASSWORD=uma_senha_forte_aqui
```

Rode o painel:

```powershell
python web_dashboard.py
```

Abra:

```text
http://127.0.0.1:8765
```

O painel usa o `xp_data.json`. Para ter ganho desde a ultima leitura, salve pelo menos duas leituras com `/top_diario salvar_leitura:True` ou deixe o `/canal_rank` rodando automaticamente.

Tambem da para clicar em **Consultar RubinOT** no painel. Esse botao chama o Python local, consulta o RubinOT, salva a leitura e atualiza os calculos.

Se o Cloudflare pedir verificacao:

```text
1. clique em Abrir verificacao
2. faca a verificacao na janela do RubinOT
3. feche essa janela
4. clique em Consultar RubinOT
```

Por padrao, esse navegador usa o perfil `chrome-profile-rubinot`, separado do seu Chrome normal. Se quiser outro caminho:

```env
RUBINOT_BROWSER_PROFILE_DIR=chrome-profile-rubinot
```

## Supabase

O painel sempre salva localmente no `xp_data.json`. Se quiser backup no Supabase, crie as tabelas rodando o SQL de [supabase_schema.sql](supabase_schema.sql) no SQL Editor do Supabase.

Depois coloque no `.env`:

```env
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_SERVICE_ROLE_KEY=sua_service_role_key
```

Use a `service_role_key` somente no backend/local, nunca em JavaScript de navegador. Quando essas variaveis existirem, cada clique em **Consultar RubinOT** tambem envia a leitura para o Supabase.

Se o `xp_data.json` local estiver vazio ou perder historico, o painel tenta carregar as ultimas leituras do Supabase automaticamente para manter o comparativo funcionando.

As PTs cadastradas pelo site tambem sao enviadas para a tabela `party_configs` quando o Supabase estiver configurado.

## Canal automatico so para ranking

Em um canal do Discord, rode:

```text
/canal_rank
/mundo_rank mundo:Cellenium
/intervalo_rank minutos:10 limite:10
```

Depois disso, o bot posta automaticamente o top `Daily Experience (raw)` daquele mundo nesse canal.

Cada atualizacao mostra:

- level atual do personagem
- XP atual exibida no ranking
- quanto ele fez de raw desde a ultima postagem do bot

Na primeira postagem, o bot mostra `primeira leitura`, porque ainda nao existe base anterior para comparar.

O intervalo minimo e 5 minutos para evitar excesso de consultas no site.

## Configuracao opcional por personagem

Definir o canal onde o bot posta atualizacoes:

```text
/canal_xp
```

Adicionar players:

```text
/adicionar_player nome:Seu Char multiplicador:1
/adicionar_player nome:Char Do Amigo multiplicador:5
```

Forcar atualizacao agora:

```text
/atualizar_xp
```

Ver ranking salvo do dia:

```text
/ranking
```

Se a primeira leitura do dia acontecer depois que alguem ja cacou, ajuste o inicio:

```text
/fixar_inicio nome:Seu Char xp:250kk
```

## Comandos

```text
/canal_xp
/canal_rank
/mundo_rank
/intervalo_rank
/top_diario
/comparar_pt
/pt_definir
/pt_remover
/pts
/categoria_diaria
/adicionar_player
/remover_player
/players
/atualizar_xp
/ranking
/fixar_inicio
/limpar_dia
```

## Observacao

O site verdadeiro do RubinOT atual e `https://rubinot.com.br`.

Por isso o bot usa `RUBINOT_BASE_URL=https://rubinot.com.br` por padrao:

```env
RUBINOT_BASE_URL=https://rubinot.com.br
```

Para comparar a PT contra o ranking, o bot usa a categoria `Daily Experience (raw)`. O ID padrao configurado e `7`, mas pode ser alterado pelo `.env` ou pelo comando `/categoria_diaria`.

Para monitorar personagens individuais, o bot usa a categoria `Experience Points` e calcula a XP do dia pela diferenca entre a primeira leitura e a leitura atual.

Se o Discord mostrar erro `403 Forbidden` ou `Just a moment...`, isso significa que o Cloudflare do RubinOT bloqueou leitura automatica por script. Nesse caso atualize o `RUBINOT_CF_CLEARANCE` no `.env` usando o cookie do navegador.
