# Publicar o Rankzada XP na Vercel

Este projeto esta preparado para a Vercel com frontend estatico em `web/` e API Python serverless em `api/index.py`.

## 1. Supabase

1. Abra o projeto no Supabase.
2. Va em SQL Editor.
3. Rode todo o conteudo de `supabase_schema.sql`.
4. Guarde `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY` para configurar na hospedagem.

## 2. Variaveis de ambiente

Configure estas variaveis no servico online:

```env
HOST=0.0.0.0
RANKZADA_PUBLIC_UPDATE=false
RANKZADA_ALLOW_BROWSER_VERIFICATION=false
RUBINOT_BASE_URL=https://rubinot.com.br
RUBINOT_DAILY_CATEGORY=7
RUBINOT_HIGHSCORE_CATEGORY=experience
RUBINOT_CF_CLEARANCE=cole_o_cookie_atualizado
RUBINOT_USER_AGENT=cole_o_user_agent_do_navegador
SUPABASE_URL=sua_url
SUPABASE_SERVICE_ROLE_KEY=sua_service_role_key
RANKZADA_ADMIN_USER=seu_usuario
RANKZADA_ADMIN_PASSWORD=sua_senha_forte
```

Mantenha `RANKZADA_PUBLIC_UPDATE=false` para visitantes apenas acompanharem o ranking. Assim, somente o admin consegue atualizar ranking e cadastrar PTs.

## 3. GitHub

1. Suba o projeto para `https://github.com/paolof1/xpzada.git`.
2. Confirme que `.env`, `xp_data.json`, logs e perfil do Chrome nao foram enviados.

## 4. Vercel

1. Entre em https://vercel.com.
2. Clique em Add New > Project.
3. Importe o repositorio `paolof1/xpzada`.
4. Framework Preset: Other.
5. Build Command: deixe vazio.
6. Output Directory: deixe vazio.
7. Adicione as variaveis de ambiente.
8. Deploy.

As rotas estao no `vercel.json`:

- `/` serve `web/index.html`
- `/app.js`, `/styles.css` e `/assets/*` servem os arquivos do site
- `/api/*` e `/health` usam a funcao Python `api/index.py`

## Cloudflare do RubinOT

Em servidor online nao e uma boa abrir navegador visivel para resolver Cloudflare. O site tenta consultar sem janela usando `RUBINOT_CF_CLEARANCE`. Se esse cookie expirar, atualize o valor nas variaveis da hospedagem e faca redeploy/restart.

Localmente, se quiser abrir a verificacao manual no seu PC, ligue:

```env
RANKZADA_ALLOW_BROWSER_VERIFICATION=true
```
