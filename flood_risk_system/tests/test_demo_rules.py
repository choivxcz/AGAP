from app.demo_rules import assess, category

HOUSE={'Age':32,'HouseMembers':4,'ChildrenCount':0,'ElderCount':0,'PWDCount':0,'HouseMaterial':'Concrete'}
VALUES={'FloodSusceptibility':'Moderate','DistanceToWaterwayM':500,'ElevationMeters':20,'SlopeDegrees':5,'Rainfall24hMm':0,'Rainfall3DayMm':0,'Rainfall7DayMm':0,'LandCoverType':'BuiltUp'}
def run(values=None,house=None):
    return assess({k:{'value':v} for k,v in {**VALUES,**(values or {})}.items()},{**HOUSE,**(house or {})})

def test_cutoffs():
    assert [category(s) for s in [0,5,6,11,12,21]]==['Low','Low','Medium','Medium','High','High']

def test_extreme_scenarios_and_medium():
    assert run()['RiskLevel']=='Low'
    assert run({'DistanceToWaterwayM':100,'ElevationMeters':5})['RiskLevel']=='Medium'
    high=run({'FloodSusceptibility':'VeryHigh','DistanceToWaterwayM':0,'ElevationMeters':1,'SlopeDegrees':0,'Rainfall24hMm':100,'Rainfall3DayMm':200,'Rainfall7DayMm':400,'LandCoverType':'HerbaceousWetland'}, {'Age':65,'HouseMembers':8,'ChildrenCount':2,'ElderCount':1,'PWDCount':1,'HouseMaterial':'LightMaterials'})
    assert high['RiskLevel']=='High'
    assert high['score_min']==high['score_max']==21

def test_missing_cannot_silently_create_low():
    result=run({'FloodSusceptibility':'NotMapped','DistanceToWaterwayM':100})
    assert result['RiskLevel'] is None
    assert result['status']=='insufficient_data'
    assert result['score_min']==3 and result['score_max']==7

def test_rainfall_not_triple_counted_and_distance_not_double_counted():
    result=run({'Rainfall24hMm':100,'Rainfall3DayMm':200,'Rainfall7DayMm':400,'LocationType':'Riverside'})
    assert result['score_min']==4
    assert result['validated'] is False
    assert 'accuracy' not in result and 'probability' not in result
