/**
 * LOCAL DEVELOPMENT / DEMO ONLY. Fictional accounts created by vtu-backend/scripts/seed.py (see its README).
 * Imported only behind `import.meta.env.DEV`, so production builds drop it. Selecting an account only fills the
 * form: sign-in still goes through POST /auth/login.
 */
export const DEMO_PASSWORD = 'vtu@123';

export const DEMO_ACCOUNTS = [
  ['Aarav Sharma · Student (70/100)', 'student@vtu.edu.in'],
  ['Diya Nair · Student (target reached)', 'student2@vtu.edu.in'],
  ['Karthik Bhat · Student (lateral, 75)', 'student5@vtu.edu.in'],
  ['Rohan Gowda · Student (disabled)', 'student6@vtu.edu.in'],
  ['Robotics Club · Club Lead', 'club@vtu.edu.in'],
  ['CS · HOD', 'hod@vtu.edu.in'],
  ['EC · HOD', 'hod.ece@vtu.edu.in'],
  ['Dean', 'dean@vtu.edu.in'],
];
