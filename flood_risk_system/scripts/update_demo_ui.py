from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'app/static/assessment.html'
s=p.read_text(encoding='utf-8')
s=s.replace('Research prototype · model pending','Unvalidated rule-based demo').replace('Review assessment →','Run demo assessment →')
s=s.replace('<h2>Prediction unavailable</h2>','<h2 id="result-title">Demo assessment</h2>')
s=s.replace('<p id="result-summary"></p>','<p id="result-summary"></p><p id="result-score"></p><details><summary>How this demo score is calculated</summary><ul id="result-rules"></ul></details>')
s=s.replace('Observed flood-outcome labels and a validated model are needed before this system can predict RiskLevel. No accuracy, risk probability or safety classification is available.','This Low / Medium / High category uses illustrative rules, not machine learning. It is not validated and must not guide evacuation or safety decisions. A Low demo category does not mean safe.')
p.write_text(s,encoding='utf-8')
p=root/'scripts/check_assessment_browser.py'
s=p.read_text(encoding='utf-8').replace("name='Review assessment'","name='Run demo assessment'").replace("assert 'Prediction unavailable' in page.locator('#result').inner_text()","assert 'unvalidated demo' in page.locator('#result-title').inner_text()\n    assert page.locator('#result-rules li').count()==10")
p.write_text(s,encoding='utf-8')
