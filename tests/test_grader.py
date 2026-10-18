from conftest import RESUME

from jobradar.grader import find_sections, grade_resume, job_fit, split_lines

WEAK = """Jordan Lee
jordan@email.com
Experience
Intern, Some Company   Jun 2024 - Aug 2024
- Responsible for helping the team with various tasks
- Worked on the website
- I was a team player and hard worker who helped my manager
Education
State University
"""

STRONG = """Sam Rivera
sam@example.com | (555) 222-3333 | Austin, TX | linkedin.com/in/samrivera

EXPERIENCE
Data Analyst, Initech                                   Jan 2024 - Present
• Built 12 Tableau dashboards used by 300+ sales reps, cutting weekly reporting time by 6 hours
• Automated invoice reconciliation in Python and SQL, reducing errors 40% across $2M in monthly billing
• Designed an A/B testing framework that lifted email conversion 18% in Q3
• Partnered with finance to forecast quarterly revenue within 3% accuracy

Analytics Intern, Globex                                Jun 2023 - Aug 2023
• Cleaned and modeled 5 years of churn data in pandas, identifying 3 drivers behind 22% of cancellations
• Presented findings to 4 directors, leading to a retention pilot that saved $150K

PROJECTS
• Developed a Flask app that forecasts bike demand with scikit-learn, reaching 0.91 R² on holdout data

EDUCATION
University of Texas at Austin, B.S. Statistics, 2023

SKILLS
Python, SQL, pandas, scikit-learn, Tableau, Power BI, Excel, statistics, A/B testing, Snowflake, dbt, Git
"""


def check(report, key):
    return next(c for c in report.checks if c.key == key)


def test_sections_detected():
    sections = find_sections(split_lines(STRONG))
    assert {"experience", "education", "skills", "projects"} <= set(sections)


def test_strong_resume_grades_well():
    report = grade_resume(STRONG, 2.5)
    assert report.score >= 85, [(c.key, c.score) for c in report.checks]
    assert report.grade.startswith(("A", "B"))
    assert check(report, "impact").status == "pass"
    assert check(report, "verbs").status == "pass"
    assert check(report, "contact").status == "pass"


def test_weak_resume_gets_specific_fixes():
    report = grade_resume(WEAK, 0.5)
    assert report.score < 50
    assert report.grade in ("D", "F")
    verbs = check(report, "verbs")
    assert verbs.status == "fail"
    assert any("Responsible for" in e for e in verbs.examples)
    assert "team player" in check(report, "buzzwords").summary
    assert check(report, "pronouns").status != "pass"
    assert "phone" in check(report, "contact").summary
    assert report.fixes[0].weight * (1 - report.fixes[0].score) >= report.fixes[-1].weight * (1 - report.fixes[-1].score)


def test_demand_drives_keyword_check():
    demand = [{"skill": "python", "count": 10, "share": 80}, {"skill": "spark", "count": 8, "share": 60},
              {"skill": "sql", "count": 6, "share": 50}]
    report = grade_resume(RESUME, 3, demand)
    keywords = check(report, "keywords")
    assert keywords.title == "Matches in-demand skills"
    assert any("spark" in tip for tip in keywords.tips)


def test_unbulleted_resume_still_finds_experience_lines():
    text = STRONG.replace("• ", "")
    report = grade_resume(text, 2)
    assert report.stats["bullets"] >= 6


def test_job_fit():
    fit = job_fit(STRONG, "Data Analyst", "We want a data analyst skilled in SQL, Looker and Python. "
                  "You will partner with marketing stakeholders. Marketing stakeholders depend on you.")
    assert "sql" in fit.matched_skills and "looker" in fit.missing_skills
    assert "marketing" in fit.missing_keywords
    assert fit.title_found
    assert 40 <= fit.score <= 95
