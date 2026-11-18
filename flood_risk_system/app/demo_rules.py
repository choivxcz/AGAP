"""Illustrative UI scoring only. Not calibrated, trained, or operational guidance."""
VERSION = 'demo-1.0'

def category(score):
    return 'Low' if score < 6 else 'Medium' if score < 12 else 'High'

def assess(features, household):
    components = []
    def add(name, value, maximum, rule):
        components.append({'factor':name,'points':value,'maximum':maximum,'rule':rule})
    def value(name):
        return features.get(name, {}).get('value')
    def numeric(name, maximum, evaluate, rule):
        v=value(name)
        add(name, None if v is None else evaluate(v),maximum,rule)
    add('FloodSusceptibility', {'Moderate':1,'High':3,'VeryHigh':4}.get(value('FloodSusceptibility')),4,'Moderate +1; High +3; VeryHigh +4. NotMapped is unknown.')
    numeric('DistanceToWaterwayM',3,lambda v:3 if v<=100 else 1 if v<=300 else 0,'≤100 m +3; >100–300 m +1; >300 m +0. LocationType is not counted again.')
    numeric('ElevationMeters',2,lambda v:2 if v<=5 else 1 if v<=10 else 0,'≤5 m +2; >5–10 m +1; >10 m +0.')
    numeric('SlopeDegrees',1,lambda v:1 if v<=2 else 0,'≤2 degrees +1; otherwise +0.')
    rain=[]
    for name,cutoffs in [('Rainfall24hMm',(25,50,100)),('Rainfall3DayMm',(50,100,200)),('Rainfall7DayMm',(100,200,400))]:
        v=value(name)
        rain.append(None if v is None else sum(v>=t for t in cutoffs))
    add('Rainfall',None if None in rain else max(rain),3,'Use the largest window score, not their sum. 24h: 25/50/100 mm; 3-day: 50/100/200 mm; 7-day: 100/200/400 mm give +1/+2/+3.')
    lc=value('LandCoverType')
    add('LandCoverType',None if lc is None else int(lc in ('HerbaceousWetland','Mangroves')),1,'HerbaceousWetland or Mangroves +1; other mapped land +0.')
    add('HouseMaterial',{'Concrete':0,'MixedMaterials':1,'LightMaterials':2}[household['HouseMaterial']],2,'Concrete +0; MixedMaterials +1; LightMaterials +2.')
    add('Household composition',sum(household[k]>0 for k in ['ChildrenCount','ElderCount','PWDCount']),3,'+1 each for any children, elders, or PWD members; maximum +3.')
    add('Age',int(household['Age']<18 or household['Age']>=60),1,'Selected resident under 18 or aged 60+ adds +1, in addition to household composition.')
    add('HouseMembers',int(household['HouseMembers']>=7),1,'7–12 members +1; 1–6 members +0.')
    low=sum(c['points'] or 0 for c in components)
    high=low+sum(c['maximum'] for c in components if c['points'] is None)
    missing=[c['factor'] for c in components if c['points'] is None]
    level=category(low) if category(low)==category(high) else None
    return {'status':'demo' if level else 'insufficient_data','RiskLevel':level,
            'method':'Unvalidated rule-based demo','version':VERSION,'validated':False,
            'score_min':low,'score_max':high,'maximum_score':21,'missing_factors':missing,
            'components':components,'thresholds':'Low: 0–5; Medium: 6–11; High: 12–21.',
            'message':('Illustrative category only. These thresholds and weights are arbitrary prototype assumptions, not a trained model or an official flood-risk standard. No accuracy or probability is available.' if level else 'A demo category cannot be determined: missing environmental values could change the category. Unknown values are not treated as evidence of low risk.')}
