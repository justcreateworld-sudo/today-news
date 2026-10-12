# Today News (free setup)

1. Create a free GitHub account, then a new **public** repository called `today-news`.
2. Upload everything in this folder (keep `.github/workflows/update-news.yml` in that path).
3. Repo **Settings > Pages**: Source = "Deploy from a branch", branch `main`, folder `/ (root)`. Your site goes live at `https://YOUR-NAME.github.io/today-news/`.
4. Repo **Settings > Actions > General > Workflow permissions**: choose "Read and write permissions".
5. Open the **Actions** tab, choose "Update news", press **Run workflow**. After about a minute `news.json` appears and the site shows real news. It then repeats every 30 minutes by itself.
6. Optional AI summaries: get a free key at aistudio.google.com, then **Settings > Secrets and variables > Actions > New secret** named `GEMINI_API_KEY`.

Add a country: add one block to `feeds.json` with its RSS feed links.
