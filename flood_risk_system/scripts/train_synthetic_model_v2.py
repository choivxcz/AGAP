"""Versioned synthetic experiment: frozen plan, validation selection, one test.

No empirical household calibration or observed flood labels are implied.
"""
import sys, json, hashlib, html, platform
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import joblib, sklearn
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, classification_report, confusion_matrix
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from app.ml_model import FEATURES, NUMERIC, CATEGORICAL

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/synthetic_ml_v2'
SEED=20261027
LABELS=['Low','Medium','High']
SOURCES={
    'static':ROOT.parent/'synthetic_dataset_tools/validated_dataset/synthetic_households_static.csv',
    'rainfall':ROOT/'data/assessment_sources/nasa_power_daily_rainfall_features_1991_2025.csv',
    'previous_dates':ROOT.parent/'synthetic_dataset_tools/validated_dataset/selected_rainfall_events.csv',
}
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def evaluate(y,p):
    report=classification_report(y,p,labels=LABELS,output_dict=True,zero_division=0)
    return dict(accuracy=float(accuracy_score(y,p)),balanced_accuracy=float(balanced_accuracy_score(y,p)),macro_f1=float(f1_score(y,p,average='macro')),classification_report=report,confusion_matrix=confusion_matrix(y,p,labels=LABELS).tolist())

def run():
    if OUT.exists(): raise RuntimeError('Output exists. Preserve the completed experiment; use a new version for a new study.')
    OUT.mkdir(parents=True)
    hashes={k:{'path':str(v),'sha256':digest(v)} for k,v in SOURCES.items()}
    plan={'seed':SEED,'target_accuracy':.98,'selection':'Highest validation macro F1, then balanced accuracy, then candidate order. Never select on test metrics.',
          'splits':'60/20/20 households within each barangay, paired with dates 1991-2012 / 2013-2018 / 2019-2025. Cross-group pairs excluded.',
          'dates':'One random complete date per calendar quarter, excluding all 70 v1 dates. No rainfall-intensity oversampling.',
          'targets':'Same illustrative point scoring as demo-1.0. Missing contributions are uniform random integers, fixed per household/factor. No target smoothing or relabeling to improve scores.',
          'age':'Choose reference resident age band proportional to generated household group counts, then uniform age within that band. Not empirically calibrated.',
          'candidates':['reference_hgb_160','hgb_400','balanced_hgb_400','extra_trees_200'],
          'parameters':{'reference_hgb_160':{'max_iter':160,'max_leaf_nodes':31,'learning_rate':.1,'l2_regularization':1},'hgb_400':{'max_iter':400,'max_leaf_nodes':63,'learning_rate':.06,'l2_regularization':2},'balanced_hgb_400':{'max_iter':400,'max_leaf_nodes':31,'learning_rate':.06,'l2_regularization':3,'class_weight':'balanced'},'extra_trees_200':{'n_estimators':200,'max_depth':24,'min_samples_leaf':3,'max_features':.8,'class_weight':'balanced','n_jobs':4}},
          'refit':'Selected hyperparameters refit on Train plus Validation, then evaluated once on Test.',
          'real_world_validation':False,'sources':hashes}
    (OUT/'experiment_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    rng=np.random.default_rng(SEED)
    hh=pd.read_csv(SOURCES['static']).sort_values('SyntheticHouseholdID').reset_index(drop=True)
    assert len(hh)==2600 and hh.DataOrigin.eq('Synthetic').all()
    # Household counts/materials and original GIS values are preserved.
    ages=[]
    for r in hh.itertuples():
        counts=np.array([r.ChildrenCount,r.HouseMembers-r.ChildrenCount-r.ElderCount,r.ElderCount])
        assert counts.min()>=0
        band=int(rng.choice(3,p=counts/counts.sum()))
        lo,hi=[(0,18),(18,60),(60,96)][band]
        ages.append(int(rng.integers(lo,hi)))
    hh['Age']=ages
    hh['HouseholdGroup']=''
    for _,group in hh.groupby('Barangay',sort=True):
        ids=group.index.to_numpy().copy();rng.shuffle(ids)
        hh.loc[ids[:60],'HouseholdGroup']='Train'
        hh.loc[ids[60:80],'HouseholdGroup']='Validation'
        hh.loc[ids[80:],'HouseholdGroup']='Test'
    points=hh.FloodSusceptibility.map({'Moderate':1,'High':3,'VeryHigh':4}).astype(float)
    unknown=points.isna().astype(int)
    base=points.fillna(pd.Series(rng.integers(0,5,len(hh)),index=hh.index))
    for name,maximum,calc in [('DistanceToWaterwayM',3,lambda x:np.where(x<=100,3,np.where(x<=300,1,0))),('ElevationMeters',2,lambda x:np.where(x<=5,2,np.where(x<=10,1,0))),('SlopeDegrees',1,lambda x:(x<=2).astype(int))]:
        p=pd.Series(calc(hh[name]),index=hh.index,dtype=float).mask(hh[name].isna())
        unknown+=p.isna().astype(int)
        base+=p.fillna(pd.Series(rng.integers(0,maximum+1,len(hh)),index=hh.index))
    base+=hh.LandCoverType.isin(['HerbaceousWetland','Mangroves']).astype(int)
    base+=hh.HouseMaterial.map({'Concrete':0,'MixedMaterials':1,'LightMaterials':2})
    base+=sum((hh[c]>0).astype(int) for c in ['ChildrenCount','ElderCount','PWDCount'])
    base+=((hh.Age<18)|(hh.Age>=60)).astype(int)+(hh.HouseMembers>=7).astype(int)
    hh['SyntheticBaseScore']=base.astype(int)
    hh['UnknownTargetContributions']=unknown
    rain=pd.read_csv(SOURCES['rainfall']).rename(columns={'date':'EventDate','rainfall_24h_mm':'Rainfall24hMm','rainfall_3day_mm':'Rainfall3DayMm','rainfall_7day_mm':'Rainfall7DayMm'})
    excluded=set(pd.read_csv(SOURCES['previous_dates']).EventDate)
    rain=rain.dropna().loc[lambda f:~f.EventDate.isin(excluded)].copy()
    date=pd.to_datetime(rain.EventDate);rain['Year']=date.dt.year;rain['Quarter']=date.dt.quarter
    selected=[int(rng.choice(g.index)) for _,g in rain.groupby(['Year','Quarter'],sort=True)]
    events=rain.loc[selected].sort_values('EventDate').copy()
    events['DateGroup']=np.where(events.Year<=2012,'Train',np.where(events.Year<=2018,'Validation','Test'))
    events['SelectionMethod']='Uniform random complete date per calendar quarter; v1 dates excluded'
    events['RainfallSource']=str(SOURCES['rainfall'])
    events.to_csv(OUT/'selected_rainfall_events.csv',index=False)
    df=hh.merge(events[['EventDate','Rainfall24hMm','Rainfall3DayMm','Rainfall7DayMm','DateGroup']],how='cross')
    windows=[]
    for col,cuts in [('Rainfall24hMm',[25,50,100]),('Rainfall3DayMm',[50,100,200]),('Rainfall7DayMm',[100,200,400])]:
        windows.append(sum((df[col]>=cut).astype(int) for cut in cuts))
    df['SyntheticTargetScore']=df.SyntheticBaseScore+np.maximum.reduce(windows)
    df['RiskLevel']=np.where(df.SyntheticTargetScore<6,'Low',np.where(df.SyntheticTargetScore<12,'Medium','High'))
    df['TargetOrigin']='Synthetic';df['TargetMethod']='Illustrative simulator v1; uniform latent missing contributions; experiment v2'
    df['Split']=np.where(df.HouseholdGroup==df.DateGroup,df.HouseholdGroup,'ExcludedCrossGroup')
    df.drop(columns='SyntheticBaseScore',inplace=True)
    df.to_csv(OUT/'synthetic_labeled_training_data.csv',index=False)
    groups={s:df[df.Split==s].copy() for s in ['Train','Validation','Test']}
    for left,right in [('Train','Validation'),('Train','Test'),('Validation','Test')]:
        assert not set(groups[left].SyntheticHouseholdID)&set(groups[right].SyntheticHouseholdID)
        assert not set(groups[left].EventDate)&set(groups[right].EventDate)
    assert not set(events.EventDate)&excluded
    assert all(set(g.RiskLevel)==set(LABELS) for g in groups.values())
    train,validation=groups['Train'],groups['Validation']
    def pipeline(name):
        prep=ColumnTransformer([('numbers',SimpleImputer(strategy='median',add_indicator=True),NUMERIC),('categories',Pipeline([('fill',SimpleImputer(strategy='constant',fill_value='Unknown')),('encode',OneHotEncoder(handle_unknown='ignore',sparse_output=False))]),CATEGORICAL)])
        params=plan['parameters'][name]
        cls=ExtraTreesClassifier(**params,random_state=SEED) if name.startswith('extra') else HistGradientBoostingClassifier(**params,early_stopping=False,random_state=SEED)
        return Pipeline([('preprocess',prep),('classifier',cls)])
    comparison=[]
    for name in plan['candidates']:
        print('Training candidate:',name,flush=True)
        candidate=pipeline(name).fit(train[FEATURES],train.RiskLevel)
        stats=evaluate(validation.RiskLevel,candidate.predict(validation[FEATURES]))
        comparison.append({'name':name,**stats})
        print(json.dumps({'name':name,'validation_accuracy':stats['accuracy'],'validation_macro_f1':stats['macro_f1']}),flush=True)
    winner=max(comparison,key=lambda m:(m['macro_f1'],m['balanced_accuracy']))
    (OUT/'validation_comparison.json').write_text(json.dumps(comparison,indent=2))
    print('Selected on validation only:',winner['name'],flush=True)
    finaltrain=pd.concat([train,validation],ignore_index=True)
    model=pipeline(winner['name']).fit(finaltrain[FEATURES],finaltrain.RiskLevel)
    # First and only final-test evaluation of the selected/refitted candidate.
    test=groups['Test'];pred=model.predict(test[FEATURES])
    metrics=evaluate(test.RiskLevel,pred)
    major=finaltrain.RiskLevel.mode()[0]
    metrics.update(version='synthetic-ml-v2',model_name=winner['name'],model_type=type(model.named_steps['classifier']).__name__,labels=LABELS,
                   train_rows=len(finaltrain),initial_train_rows=len(train),validation_rows=len(validation),test_rows=len(test),excluded_rows=int((df.Split=='ExcludedCrossGroup').sum()),
                   train_households=finaltrain.SyntheticHouseholdID.nunique(),test_households=test.SyntheticHouseholdID.nunique(),total_rows=len(df),
                   seed=SEED,features=FEATURES,majority_baseline_accuracy=float((test.RiskLevel==major).mean()),
                   target_origin='Synthetic',validation_scope='Synthetic simulation only; not observed flood validation',
                   accuracy_target=.98,accuracy_target_met=bool(metrics['accuracy']>=.98),source_manifest=hashes,sklearn_version=sklearn.__version__,python_version=platform.python_version())
    slices={}
    for name,mask in [('mapped_susceptibility',test.FloodSusceptibility.ne('NotMapped')),('unmapped_susceptibility',test.FloodSusceptibility.eq('NotMapped'))]:
        if mask.any(): slices[name]={'rows':int(mask.sum()),**evaluate(test.RiskLevel[mask],pred[mask.to_numpy()])}
    metrics['subgroup_metrics']=slices
    metrics['validation_selection']=winner['name']
    joblib.dump(model,OUT/'model.joblib')
    assert np.array_equal(pred,joblib.load(OUT/'model.joblib').predict(test[FEATURES]))
    test[['SyntheticHouseholdID','EventDate','Barangay','RiskLevel','TargetOrigin','FloodSusceptibility']].assign(PredictedRiskLevel=pred).to_csv(OUT/'test_predictions.csv',index=False)
    assert all(digest(SOURCES[k])==v['sha256'] for k,v in hashes.items())
    (OUT/'metrics.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    quality={'source_hashes_unchanged':True,'source_datasets_overwritten':False,'household_and_date_splits_disjoint':True,'v1_event_dates_excluded':True,
             'all_records_synthetic':bool(df.DataOrigin.eq('Synthetic').all() and df.TargetOrigin.eq('Synthetic').all()),
             'count_constraints':bool((hh.ChildrenCount+hh.ElderCount+hh.PWDCount<=hh.HouseMembers).all()),
             'reference_age_consistent':bool((((hh.Age<18)&(hh.ChildrenCount>0))|((hh.Age>=60)&(hh.ElderCount>0))|((hh.Age.between(18,59))&(hh.HouseMembers>hh.ChildrenCount+hh.ElderCount))).all()),
             'no_target_columns_in_features':not bool(set(FEATURES)&{'RiskLevel','SyntheticTargetScore','Split','TargetOrigin','UnknownTargetContributions'}),
             'households_per_barangay':hh.groupby('Barangay').size().to_dict(),'null_counts':df.isna().sum().to_dict(),
             'class_counts_by_split':df.groupby(['Split','RiskLevel']).size().unstack(fill_value=0).to_dict('index')}
    (OUT/'quality_report.json').write_text(json.dumps(quality,indent=2),encoding='utf-8')
    render(metrics,comparison,df,quality)
    print('FINAL TEST',json.dumps({k:metrics[k] for k in ['model_name','accuracy','macro_f1','balanced_accuracy','accuracy_target_met','total_rows','train_rows','test_rows']}),flush=True)

def render(m,comparison,df,quality):
    matrix=np.asarray(m['confusion_matrix']);norm=matrix/matrix.sum(axis=1,keepdims=True)
    fig,axes=plt.subplots(1,2,figsize=(11,4.8),layout='constrained')
    for ax,values,title in [(axes[0],matrix,'Counts'),(axes[1],norm,'Row-normalized recall')]:
        ax.imshow(values,cmap='Blues');ax.set(xticks=range(3),yticks=range(3),xticklabels=LABELS,yticklabels=LABELS,xlabel='Predicted synthetic class',ylabel='Synthetic target class',title=title)
        for i in range(3):
            for j in range(3): ax.text(j,i,str(values[i,j]) if values is matrix else f'{values[i,j]:.1%}',ha='center',va='center',color='white' if values[i,j]>values.max()/2 else '#163840')
    fig.suptitle('Synthetic experiment v2 — no observed flood outcomes',fontweight='bold');fig.savefig(OUT/'confusion_matrix.png',dpi=160);plt.close(fig)
    report=pd.DataFrame({k:m['classification_report'][k] for k in LABELS}).T
    report['support']=report.support.astype(int)
    candidates=pd.DataFrame([{k:r[k] for k in ['name','accuracy','macro_f1','balanced_accuracy']} for r in comparison])
    candidates.columns=['Candidate','Validation accuracy','Validation macro F1','Validation balanced accuracy']
    high=m['classification_report']['High']
    status='MET ON SYNTHETIC TEST ONLY' if m['accuracy_target_met'] else 'NOT MET'
    body=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Synthetic ML v2 evaluation</title><style>body{{font:16px system-ui;background:#f1f5f4;color:#183c40;margin:0}}main{{max-width:1100px;margin:auto;padding:28px}}section{{background:white;border:1px solid #d5e1dc;padding:24px;border-radius:14px;margin:20px 0}}.notice{{border-left:5px solid #b27b24}}.cards{{display:flex;gap:28px;flex-wrap:wrap}}strong{{font-size:28px;display:block}}table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #ddd}}img{{width:100%}}p,li{{line-height:1.7}}.scroll{{overflow-x:auto}}</style><main><p>SANTA CRUZ · SYNTHETIC ML v2</p><h1>Independent experiment report</h1><section class="notice"><h2>98% accuracy target: {status}</h2><p>Measured test accuracy: {m['accuracy']:.2%}. No confusion-matrix pass threshold was supplied; per-class results are disclosed below. These targets are simulated, not actual flood outcomes. The dataset is not official LGU/PSA household data, and this is not an operational safety model.</p></section><section class="cards"><div><strong>{m['accuracy']:.2%}</strong>Test accuracy</div><div><strong>{m['macro_f1']:.3f}</strong>Macro F1</div><div><strong>{m['balanced_accuracy']:.2%}</strong>Balanced accuracy</div><div><strong>{m['majority_baseline_accuracy']:.2%}</strong>Majority baseline</div><div><strong>{high['recall']:.2%}</strong>High-class recall</div></section><section><h2>Test confusion matrices</h2><img src="confusion_matrix.png" alt="Raw and row-normalized confusion matrices"><div class="scroll">{report.to_html(float_format=lambda n:f'{n:.3f}',border=0)}</div></section><section><h2>Validation-only selection</h2><p>Selected {html.escape(m['model_name'])} by macro F1, then balanced accuracy. No test-set tuning or repeated test-based search. Reference_hgb_160 is the previous model configuration retrained on the new training subset; old 81.6% results are not directly comparable because the dataset changed.</p><div class="scroll">{candidates.to_html(index=False,float_format=lambda n:f'{n:.4f}',border=0)}</div><p>Final model refit on {m['train_rows']:,} Train + Validation rows and evaluated once on {m['test_rows']:,} Test rows. Initial split: {m['initial_train_rows']:,} Train; {m['validation_rows']:,} Validation; {m['test_rows']:,} Test. Household IDs and rainfall dates are disjoint. The 140 selected dates exclude every v1 event date.</p></section><section><h2>Dataset changes and limits</h2><p>{m['total_rows']:,} hypothetical household/date scenarios. One complete rainfall date sampled uniformly per calendar quarter, not annual maxima. Age band selection is proportional to each synthetic household's member counts, then uniform within child/adult/elder bands. Original GIS values and synthetic composition/materials are preserved. These consistency changes do not establish demographic or physical realism.</p><p>The target simulator remains the illustrative v1 scoring rules; no labels were rewritten to make classification easier. Missing-source contributions remain hidden uniform integer draws per household/factor. The model cannot reliably infer independent latent contributions. Missing environment values remain missing; training-only medians handle them in the pipeline. Accuracy may therefore be limited even for this synthetic task.</p><p>Coverage is equal by barangay, not population weighted. Hypothetical locations are not verified residences. Historical rainfall is one shared NASA grid estimate; static land cover does not reconstruct earlier conditions. Household/date repetitions induce dependence, so no independent-row confidence intervals are claimed. Source manifests and quality_report.json are provided.</p><h2>What is still needed for real validation</h2><p>Observed household/event outcomes, consistent Low/Medium/High definitions, a representative sampling frame and independent external evaluation. A 98% synthetic score would not solve these requirements.</p><p>Method reference: <a href="https://scikit-learn.org/stable/common_pitfalls.html">scikit-learn data leakage guidance</a>; <a href="https://scikit-learn.org/stable/modules/model_evaluation.html">classification evaluation documentation</a>.</p></section></main></html>'''
    (OUT/'report.html').write_text(body,encoding='utf-8')
    text=f'''# Synthetic ML experiment v2

98% accuracy target: {status}. Measured synthetic test accuracy {m['accuracy']:.2%}, macro F1 {m['macro_f1']:.3f}, High recall {high['recall']:.2%}. This is not validation on real outcomes.

DataOrigin=Synthetic and TargetOrigin=Synthetic on every scenario. No names, phone numbers, addresses or real-person identifiers were generated. Source files remain unchanged. The original v1 experiment is preserved.

See experiment_plan.json for the frozen plan and source SHA256 hashes; validation_comparison.json for model selection; quality_report.json for constraints, missingness, categories and split counts; metrics.json and test_predictions.csv for final results. report.html and confusion_matrix.png present the evaluation.

The 364,000 rows represent 2,600 synthetic locations crossed with 140 historical dates. Synthetic age is person-weighted by household age groups. Rainfall is sampled by calendar quarter, excluding previous dates. Source environmental and household distributions are not empirically calibrated. They cannot be claimed as real household observations.

The Low/Medium/High simulator is unchanged from demo-1.0. Same thresholds and unknown latent contributions; full implementation in scripts/train_synthetic_model_v2.py. Target scores and missing-contribution metadata are audit fields only, never predictors. Allowed inputs remain the 15 fields recorded in metrics.json.

Train/Validation/Test use disjoint household groups within each barangay and disjoint chronological periods. Select only with validation macro F1, refit with Train+Validation, test once. Compare models only on the same validation set; v1 test numbers belong to a different experiment. No guarantee of a 'passing' confusion matrix or 98% accuracy.

Reproduce with python scripts/train_synthetic_model_v2.py from flood_risk_system using pinned compatible sklearn. Output directory must not already exist; originals and previous runs are never overwritten. No model is automatically activated by this experiment script.
'''
    (OUT/'methodology.md').write_text(text,encoding='utf-8')
    (OUT/'model_card.md').write_text(text,encoding='utf-8')

if __name__=='__main__': run()
