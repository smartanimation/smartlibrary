import pytest
from smartlib.dcc.houdini.crowd_workspace import conversion

@pytest.mark.parametrize('axis,rotation', [('Y',0),('Z',-90)])
def test_working_conventions(axis,rotation):
    c=conversion({'meters_per_unit':.01,'up_axis':axis})
    assert c['scale_to_work']==.01
    assert c['scale_to_stage']==100
    assert c['rotate_x_to_work']==rotation
    assert c['rotate_x_to_stage']==-rotation

@pytest.mark.parametrize('unit,axis',[(0,'Y'),(-1,'Y'),(float('nan'),'Y'),(float('inf'),'Y'),(.01,'X')])
def test_invalid_conventions(unit,axis):
    with pytest.raises(ValueError):conversion({'meters_per_unit':unit,'up_axis':axis})
