# 🍽️ Meal Manager

Full-featured Django meal management system with role-based access, active-month switching, special meal multipliers, unlimited deposits, expense tracking, reporting, and a modern dark/light UI.

> **Rebranding:** the app name shown throughout the UI (nav logo, page titles, login screen, exported reports) is a single setting — `APP_NAME` / `APP_TAGLINE` in `config/settings.py` — not hardcoded per page. Change those two lines to re-brand for a different mess/hostel.

---

## ⚡ Quick Start

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed
python manage.py runserver
```
→ **http://127.0.0.1:8000**

---

## 🔑 Login Credentials (after seed)

| Role        | Phone       | Password | Access |
|-------------|-------------|----------|--------|
| Manager     | 01711000001 | 1234     | Full   |
| Sub Manager | 01711000002 | 1234     | Configurable |
| Members     | 0171100000X | 1234     | Read-only |

Login with **phone number + password**.

---

## 📅 Active Month Feature

The app has a **global active month** concept — change it once in Settings and every page (dashboard, meals, deposits, expenses, reports) automatically shows data for that month.

### 3 ways to change the active month:
1. **Settings page** (`/meals/settings/`) — full page with quick-jump buttons
2. **Topbar dropdown** — click the `📅 Jun 2026` pill in the top-right (Manager only)
3. **Sidebar indicator** — click the active month pill at the bottom of the sidebar

### How it works:
- Active month is stored in the **Django session** per logged-in user
- All views call `get_active_my(request)` which reads from session
- URL params `?month=X&year=Y` still override the session (per-page selectors work normally)
- The context processor injects `active_month`, `active_year`, `active_month_name` into every template

---

## ✨ Key Features

### 🍽️ Meals
- Per-member daily count: 0, 1, 2, 3+ (decimal supported, e.g. 1.5)
- **Special Meal Entry** (`/meals/day-config/`) — set ×2, ×3 multipliers for any date (feast, holiday)
- **Meal Count Settings** (`/meals/settings/`) — global per-slot weight (e.g. 1 lunch mark = 2 effective meals), applied everywhere via `MealMark.effective_count()`
- **Monthly Matrix** — full 31-day grid with sticky member column
- Day chips with colour coding (active / has data / special)

### 💰 Finance
- **Unlimited deposits** per member, stored by date + method (cash/bKash/Nagad/Rocket/bank)
- **Multiple expenses per day**, searchable by description + category
- Auto-calculated meal rate, per-member cost, balance

### 📊 Reports
- Monthly + daily reports, member statements
- 6/12-month trend charts, category doughnut, top depositors
- Export to **CSV** and **Excel** (.xlsx)

### 🔐 RBAC
- **Manager** — full access, configure sub-manager permissions
- **Sub Manager** — 10 configurable permissions
- **Member** — read-only

### 🌙 UI/UX
- Dark / Light mode toggle (persisted)
- Mobile-responsive sidebar
- Toast notifications, modal dialogs
- Audit log, notification system

---

## 📡 REST API

```
GET /api/stats/        Dashboard stats
GET /api/members/      Per-member breakdown
GET /api/meal-grid/    Full meal mark grid
GET /api/deposits/     Deposit list
GET /api/expenses/     Expense list
```
All accept `?month=M&year=Y`.

---

## 📁 Structure

```
config/          Settings, root URLs
accounts/        Auth, members, RBAC, notifications, audit log
  month_helpers.py  get_active_my(), set_active_my() — shared helpers
meals/           Meal marks, day config, announcements, monthly settings
finance/         Deposits, expenses, member statements
reports/         Monthly/daily reports, analytics, CSV/Excel export
api/             DRF REST endpoints
templates/       All HTML (base + 18 child templates)
```
