# RecoverAI dashboard

Next.js App Router dashboard for the RecoverAI FastAPI API.

## Install

```bash
cd frontend
npm install
cp .env.example .env.local
```

Set `NEXT_PUBLIC_API_BASE_URL` in `.env.local` if the FastAPI service is not running at `http://127.0.0.1:8000`.

## Run

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Keep the FastAPI backend running so the dashboard can load recovery cases.
