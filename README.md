# Part_2_AI Engineer_Take_Home Challenge
AI Engineer_Take_Home Challenge

This repository contains the implementation of **Part 2: AI Job Source Agent**.  
The agent automatically extracts job-source information starting from **LinkedIn job posting URLs**, without requiring login.

The system performs:

1. **Scraping** the LinkedIn job page to extract:  
   - Company name  
   - Company website URL  
2. **Navigating** to the official company website  
3. **Detecting** the company’s career page using an AI Web Agent  
4. **Extracting** one open job posting URL  
5. **Saving** the final output in CSV format

---

## Project Structure

```
Demo_Part_2/
│
├── part2_agent.py         # Main execution script
├── requirements.txt       # Dependencies
├── part2_results.csv      # Output file (auto-generated)
└── README.md
```

---

## 1. Environment Setup

### Create and activate a virtual environment

**Windows**
```
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux**
```
python3 -m venv venv
source venv/bin/activate
```

---

## 2. Install Dependencies

```
pip install -r requirements.txt
```

Install Playwright browsers:

```
playwright install
```

---

## 3. Running the Agent

To run the pipeline for a single LinkedIn job posting:

```
python part2_agent.py "https://www.linkedin.com/jobs/view/123456789/"
```

The script will:

- Scrape LinkedIn  
- Use an AI Web Agent to identify the career page  
- Extract one open role  
- Write the output to:

```
part2_results.csv
```

---

## 4. Output Format

Each row of `part2_results.csv` follows the structure:

```
company_name,career_page_url,open_position_url
```