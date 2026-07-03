# bloombgg
- https://django-pricing-engine-109577535661.us-central1.run.app
====

## Proposed UI & Integration Tasks

The following steps are designed as small, executable components to integrate the pricing engine and technical documentation into the Django web application.

### Phase 1: Smart Form Toggles & Defaults
* **1.1 Implement ELN Mode Toggles:** 
  - On the `/elns` calculator page, add a UI toggle to switch between **Solving Mode** and **Pricing Mode**.
  - Add JavaScript logic to disable the "Coupon Rate" input field when "Solving Mode" is selected, as the engine will solve for this value.
* **1.2 Lock Volatility Surface for Autocalls:**
  - On the `/elns` page, automatically set the Volatility Surface input to **SSVI** and show a warning if the user attempts to select SVI (to prevent arbitrage in long-dated simulations).
* **1.3 Lock Diffusion Model:**
  - Default the model to **Black-Scholes**. Add a UI warning if the user selects **Heston** for Path-Dependent/ELN products, indicating it will be significantly slower.

### Phase 2: Documentation Integration
* **2.1 Port HTML Docs to Django Templates:**
  - Move the static HTML files from the `/docs` directory into the `core/templates/core/` directory.
  - Update the files to `{% extends 'core/base.html' %}` so they inherit the global navigation bar, authentication state, and the live UTC clock.
* **2.2 Configure Routing:**
  - Update `core/urls.py` and `core/views.py` to route to the newly integrated documentation pages.

### Phase 3: Market Data Optimization
* **3.1 Global Market Object Initialization:**
  - Since initializing the `Market` object (loading CSVs, bootstrapping yield curves) takes 1-3 seconds, it should not run on every user request.
* **3.2 Implement Caching:**
  - Use a module-level Python singleton or Django's built-in caching framework in `views.py` to hold the initialized `Market` instance in memory across requests.

### Phase 4: Charting Visualization
* **4.1 Setup JavaScript Charting:**
  - Include a charting library (such as Chart.js or Plotly) on the calculator result pages.
  - Apply the terminal aesthetic (dark background, amber/green lines) to the chart configuration.
* **4.2 Render Analytical Data:**
  - Render Monte Carlo simulation paths, Volatility Surfaces, or Payoff diagrams dynamically based on the JSON output returned from the backend.
