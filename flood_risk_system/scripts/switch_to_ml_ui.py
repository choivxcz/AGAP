from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'app/static/assessment.html'
s=p.read_text(encoding='utf-8').replace('Unvalidated rule-based demo','Synthetic-label ML prototype').replace('Run demo assessment','Predict prototype risk').replace('Demo assessment','ML prototype assessment').replace('How this demo score is calculated','Model and synthetic evaluation details')
s=s.replace('This Low / Medium / High category uses illustrative rules, not machine learning. It is not validated and must not guide evacuation or safety decisions. A Low demo category does not mean safe.','This classifier was trained on synthetic targets, not observed flood outcomes. It is not validated for real residents. Low does not mean safe. <a href="/api/assessment/model-report" target="_blank" rel="noopener">View synthetic ML evaluation and confusion matrix</a>')
p.write_text(s,encoding='utf-8')
p=root/'scripts/check_assessment_browser.py'
s=p.read_text(encoding='utf-8').replace("name='Run demo assessment'","name='Predict prototype risk'").replace("'unvalidated demo' in","'synthetic ML prototype' in").replace("count()==10","count()==6").replace('unvalidated demo category and score breakdown','synthetic ML prediction and model details')
p.write_text(s,encoding='utf-8')
p=root/'README.md';s=p.read_text(encoding='utf-8').replace('An explicitly unvalidated rule-based demo produces Low, Medium or\nHigh categories with a visible score breakdown. This is not ML inference.','A trained histogram gradient boosting classifier produces Low, Medium or\nHigh categories from synthetic targets. Test metrics describe simulation only;\nthere is no validation against real flood outcomes.');p.write_text(s,encoding='utf-8')
