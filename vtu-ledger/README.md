# RVCE Activity Points Ledger

React 18 + Vite + Tailwind CSS 3 + React Router 6 + Lucide React.

    cp .env.example .env    # VITE_API_BASE_URL=http://localhost:8002 (the isolated frontend API; see vtu-backend/README.md)
    npm install
    npm run dev             # http://localhost:5173/login (port is fixed: it is the origin the API allows via CORS)
    npm run build

Sign-in uses the backend (`POST /auth/login`); the JWT is kept in `sessionStorage`. In development the login page
offers a quick-select of the fictional seeded accounts (password `vtu@123`, LOCAL DEVELOPMENT / DEMO ONLY); it only
fills the form and is removed from production builds.

Integration status: login, session, route guards and the Student dashboard use the API. The other screens still use
mock data from `src/data/mock.js` (held in React state, resets on refresh) until they are migrated.

Structure: `src/api/client.js` (fetch wrapper, `ApiError`, downloads) · `src/hooks/useApi.js` (loading/error/cancel) ·
`src/utils/format.js` (points, dates, status labels) · `src/context/` (Auth, Toast, mock App data) ·
`src/components/` (Layout, shared UI incl. `Badge`, `Loading`, `ErrorState`) · `src/pages/` · `src/dev/` (dev-only demo accounts).
