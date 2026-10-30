# RVCE Activity Points Ledger

React 18 + Vite + Tailwind CSS 3 + React Router 6 + Lucide React.

    npm install
    npm run dev      # http://localhost:5173/login
    npm run build

Demo logins (LOCAL DEVELOPMENT / DEMO ONLY, password `vtu@123`): student@ / club@ / dean@ / hod@ + `vtu.edu.in`.
All data is mock and held in React state (resets on refresh).

Structure: `src/data/mock.js` (mock data) · `src/context/AppContext.jsx` (global state) ·
`src/components/` (Layout, shared UI) · `src/pages/` (Login, Student, Club, Dean, Hod) · `tailwind.config.js` (palette tokens).
