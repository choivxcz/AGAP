"""Train once on explicitly simulated targets; originals are read-only."""
import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import joblib,sklearn
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score,balanced_accuracy_score,f1_score,classification_report,confusion_matrix
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from app.ml_model import NUMERIC,CATEGORICAL,FEATURES

# Preserve the original v1 experiment independently of the active app model.
MODEL_DIR=Path(__file__).resolve().parents[1]/'data'/'synthetic_ml_v1'

SEED=20261026
source=Path(__file__).resolve().parents[2]/'synthetic_dataset_tools/validated_dataset/synthetic_households_event_features.csv'
if MODEL_DIR.exists(): raise RuntimeError('Refusing to overwrite an existing model run')
MODEL_DIR.mkdir(parents=True)
rng=np.random.default_rng(SEED)
source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
df=pd.read_csv(source)
hh=df.drop_duplicates('SyntheticHouseholdID').set_index('SyntheticHouseholdID')
age={}
for sid,r in hh.iterrows():
    # Reference resident must be represented in the age-group counts.
    bands=[]
    if r.ChildrenCount: bands.append((0,18))
    if r.ElderCount: bands.append((60,96))
    if r.HouseMembers-r.ChildrenCount-r.ElderCount>0: bands.append((18,60))
    a,b=bands[int(rng.integers(len(bands)))];age[sid]=int(rng.integers(a,b))
df['Age']=df.SyntheticHouseholdID.map(age)
score=pd.Series(0,index=df.index,dtype=float)
def add(points,maximum):
    global score
    # Unknown environmental contribution is a SIMULATED latent value, constant
    # per household/factor; no missing real environmental value is overwritten.
    latent=dict(zip(hh.index,rng.integers(0,maximum+1,len(hh))))
    score+=points.fillna(df.SyntheticHouseholdID.map(latent))
add(df.FloodSusceptibility.map({'Moderate':1,'High':3,'VeryHigh':4}),4)
for col,maxpoints,calc in [('DistanceToWaterwayM',3,lambda x:np.where(x<=100,3,np.where(x<=300,1,0))),('ElevationMeters',2,lambda x:np.where(x<=5,2,np.where(x<=10,1,0))),('SlopeDegrees',1,lambda x:(x<=2).astype(int))]:
    pts=pd.Series(calc(df[col]),index=df.index,dtype=float).mask(df[col].isna())
    add(pts,maxpoints)
rain=[]
for col,cuts in [('Rainfall24hMm',[25,50,100]),('Rainfall3DayMm',[50,100,200]),('Rainfall7DayMm',[100,200,400])]:
    rain.append(sum((df[col]>=cut).astype(int) for cut in cuts))
score+=np.maximum.reduce(rain)
score+=df.LandCoverType.isin(['HerbaceousWetland','Mangroves']).astype(int)
score+=df.HouseMaterial.map({'Concrete':0,'MixedMaterials':1,'LightMaterials':2})
score+=sum((df[c]>0).astype(int) for c in ['ChildrenCount','ElderCount','PWDCount'])
score+=((df.Age<18)|(df.Age>=60)).astype(int)+(df.HouseMembers>=7).astype(int)
df['SyntheticTargetScore']=score.astype(int)
df['RiskLevel']=np.where(score<6,'Low',np.where(score<12,'Medium','High'))
df['TargetOrigin']='Synthetic'
df['TargetMethod']='Illustrative simulator v1; unknown contributions sampled uniformly'
df['DataOrigin']='Synthetic'
ids=np.array(sorted(hh.index));rng.shuffle(ids)
test_ids=set(ids[:len(ids)//5])
late=pd.to_datetime(df.EventDate).dt.year>=2019
df['Split']='ExcludedCrossGroup'
df.loc[~df.SyntheticHouseholdID.isin(test_ids)&~late,'Split']='Train'
df.loc[df.SyntheticHouseholdID.isin(test_ids)&late,'Split']='Test'
train=df[df.Split=='Train'];test=df[df.Split=='Test']
assert not set(train.SyntheticHouseholdID)&set(test.SyntheticHouseholdID)
assert not set(train.EventDate)&set(test.EventDate)
assert set(train.RiskLevel)==set(test.RiskLevel)=={'Low','Medium','High'}
df.to_csv(MODEL_DIR/'synthetic_labeled_training_data.csv',index=False)
prep=ColumnTransformer([('numbers',SimpleImputer(strategy='median',add_indicator=True),NUMERIC),('categories',Pipeline([('fill',SimpleImputer(strategy='constant',fill_value='Unknown')),('encode',OneHotEncoder(handle_unknown='ignore',sparse_output=False))]),CATEGORICAL)])
model=Pipeline([('preprocess',prep),('classifier',HistGradientBoostingClassifier(max_iter=160,max_leaf_nodes=31,learning_rate=.1,l2_regularization=1,early_stopping=False,random_state=SEED))])
print('Fitting',len(train),'training rows;',len(test),'held-out rows',flush=True)
model.fit(train[FEATURES],train.RiskLevel)
pred=model.predict(test[FEATURES]);labels=['Low','Medium','High']
matrix=confusion_matrix(test.RiskLevel,pred,labels=labels)
metrics={'accuracy':accuracy_score(test.RiskLevel,pred),'balanced_accuracy':balanced_accuracy_score(test.RiskLevel,pred),'macro_f1':f1_score(test.RiskLevel,pred,average='macro'),'majority_baseline_accuracy':float((test.RiskLevel==train.RiskLevel.mode()[0]).mean()),'classification_report':classification_report(test.RiskLevel,pred,labels=labels,output_dict=True,zero_division=0),'confusion_matrix':matrix.tolist(),'labels':labels,'train_rows':len(train),'test_rows':len(test),'excluded_rows':int((df.Split=='ExcludedCrossGroup').sum()),'train_households':train.SyntheticHouseholdID.nunique(),'test_households':test.SyntheticHouseholdID.nunique(),'seed':SEED,'source_sha256':source_hash,'sklearn_version':sklearn.__version__,'features':FEATURES,'target_origin':'Synthetic','validation_scope':'Synthetic simulation only; no real-world validation'}
(MODEL_DIR/'metrics.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
joblib.dump(model,MODEL_DIR/'model.joblib')
reloaded=joblib.load(MODEL_DIR/'model.joblib')
assert np.array_equal(pred,reloaded.predict(test[FEATURES]))
assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
test[['SyntheticHouseholdID','EventDate','RiskLevel','TargetOrigin']].assign(PredictedRiskLevel=pred).to_csv(MODEL_DIR/'test_predictions.csv',index=False)
fig,axes=plt.subplots(1,2,figsize=(11,4.8),layout='constrained')
for ax,values,title in [(axes[0],matrix,'Confusion matrix · counts'),(axes[1],matrix/matrix.sum(axis=1,keepdims=True),'Confusion matrix · row normalized')]:
    ax.imshow(values,cmap='Blues');ax.set(xticks=range(3),yticks=range(3),xticklabels=labels,yticklabels=labels,xlabel='Predicted synthetic class',ylabel='Synthetic target class',title=title)
    for i in range(3):
        for j in range(3): ax.text(j,i,str(values[i,j]) if values is matrix else f'{values[i,j]:.1%}',ha='center',va='center',color='white' if values[i,j]>values.max()/2 else '#173345')
fig.suptitle('Synthetic ML prototype · not real-world flood validation',fontweight='bold')
fig.savefig(MODEL_DIR/'confusion_matrix.png',dpi=160);plt.close(fig)
rows=''.join(f'<tr><th>{label}</th><td>{metrics["classification_report"][label]["precision"]:.3f}</td><td>{metrics["classification_report"][label]["recall"]:.3f}</td><td>{metrics["classification_report"][label]["f1-score"]:.3f}</td><td>{int(metrics["classification_report"][label]["support"]):,}</td></tr>' for label in labels)
report=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Synthetic ML evaluation</title><style>body{{font:16px system-ui;background:#f1f5f4;color:#183c40;margin:0}}main{{max-width:1050px;margin:auto;padding:36px}}section{{background:white;border:1px solid #d5e1dc;padding:24px;border-radius:14px;margin:20px 0}}.notice{{border-left:5px solid #b27b24}}.cards{{display:flex;gap:30px;flex-wrap:wrap}}strong{{font-size:28px;display:block}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}img{{width:100%}}p{{line-height:1.7}}</style><main><p>SANTA CRUZ · SYNTHETIC ML PROTOTYPE</p><h1>Classifier evaluation</h1><section class="notice"><b>Simulation results only</b><p>The targets were generated by an illustrative simulator. This trained classifier learns that simulator. These results do not measure actual flood-risk accuracy, resident safety, or generalization to real outcomes. No 98% guarantee is made.</p></section><section class="cards"><div><strong>{metrics['accuracy']:.1%}</strong>Synthetic test accuracy</div><div><strong>{metrics['macro_f1']:.3f}</strong>Macro F1</div><div><strong>{metrics['balanced_accuracy']:.1%}</strong>Balanced accuracy</div><div><strong>{metrics['majority_baseline_accuracy']:.1%}</strong>Majority baseline</div></section><section><h2>Held-out confusion matrices</h2><img src="confusion_matrix.png" alt="Raw and row-normalized confusion matrices for synthetic targets"><table><thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th><th>Support</th></tr></thead><tbody>{rows}</tbody></table></section><section><h2>Experiment design</h2><p>Histogram gradient boosting, fixed hyperparameters, seed {SEED}. {len(train):,} training rows from {len(set(train.SyntheticHouseholdID)):,} households and dates through 2018. {len(test):,} test rows from {len(test_ids)} different households and dates in 2019–2025. Both household IDs and event dates are disjoint. Cross-group combinations are excluded. No test-set tuning was performed. Preprocessing was fitted only on training rows.</p><p>Features: {', '.join(FEATURES)}. IDs, coordinates, target scores, dates, and provenance fields are excluded from model inputs. Missing numeric values use training medians; categories are one-hot encoded. Household and selected-date repetition still induce dependence, so no independent-observation confidence interval is claimed.</p><h2>Synthetic target generation</h2><p>Targets use the documented demo-1.0 score: MGB 0–4, waterway distance 0–3, elevation 0–2, slope 0–1, maximum rainfall-window contribution 0–3, wetland/mangroves 0–1, material 0–2, household composition 0–3, selected resident age 0–1, household size 0–1. Low 0–5; Medium 6–11; High 12–21. These are arbitrary assumptions. Unknown MGB or numeric contributions are sampled uniformly from 0 to that factor's maximum, separately per household/factor. They are simulated latent contributions, not repaired environmental observations. Original environmental missing values remain missing.</p><p>Age is a synthetic reference resident selected from age bands present in each household (child 0–17, adult 18–59, elder 60–95). No demographic calibration is claimed. All records carry DataOrigin=Synthetic and TargetOrigin=Synthetic. Labels and fitted artifacts are stored separately; originals are unchanged.</p></section></main></html>'''
(MODEL_DIR/'report.html').write_text(report,encoding='utf-8')
(MODEL_DIR/'model_card.md').write_text('# Synthetic-target ML prototype\n\n'+json.dumps(metrics,indent=2)+'\n\nSee report.html for target assumptions, splits, and limitations. No validation on real outcomes. Never treat synthetic accuracy as field accuracy. No user household input is saved.\n',encoding='utf-8')
print(json.dumps({k:metrics[k] for k in ['accuracy','macro_f1','balanced_accuracy','train_rows','test_rows']},indent=2))
