import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import sys
import pytest
from smartlib.apps.shot_manager.service import ShotManagerService
from smartlib.apps.shot_manager.shot_list import ShotListService, parse_rows, sheet_location
from smartlib.core.config_loader import ProjectConfig
from smartlib.core.metadata import read_json


@pytest.fixture
def service(tmp_path):
    config = tmp_path / 'config'
    config.mkdir()
    (config/'templates_base.yml').write_text(
        f"anchors:\n  project_name: TEST\n  project_root: '{tmp_path.as_posix()}/project'\n", encoding='utf-8')
    return ShotListService(ShotManagerService(ProjectConfig(config)))


def rows(*data):
    return parse_rows([['episode','sequence','shot','status','description'], *data],
                      {'kind':'shot_list','spreadsheet_id':'test','worksheet_id':0})


def test_url_gid_and_legacy_id():
    assert sheet_location({'shot_list_url':'https://docs.google.com/spreadsheets/d/abc/edit?gid=3#gid=0'}) == ('abc',0)
    assert sheet_location({'shot_list_id':'abc'}) == ('abc',None)
    with pytest.raises(ValueError):
        sheet_location({'shot_list_url':'https://example.com/spreadsheets/d/abc/edit'})


def test_create_then_repeat_preserves_local_data(service):
    row = rows(['crowds','school','test01','','placement test'])[0]
    identity = service.create(row)
    assert identity in service.shots.list_shots()
    path = service.shots.paths.artifact_file(service.shots.shot_root(identity),'shot.json')
    data = read_json(path,{})
    assert data['editorial']['cut_in'] == 1001
    assert data['editorial']['cut_out'] == 1240
    assert data['description'] == 'placement test'
    assert data['shot_list_source']['row'] == 2
    original = path.read_bytes()
    assert service.state(row) == 'Existing'
    with pytest.raises(ValueError, match='Existing'):
        service.create(row)
    assert path.read_bytes() == original


@pytest.mark.parametrize('name', ['../bad', 'all', '', 'a/b'])
def test_invalid_identity_rejected(service,name):
    row = rows(['ep','seq',name,'',''])[0]
    with pytest.raises(ValueError):
        service.create(row)
    assert service.shots.list_shots() == []


def test_duplicates_are_all_blocked_and_zeroes_preserved(service):
    items=rows(['001','020','003','',''],['001','020','003','',''])
    assert all(row.error for row in items)
    assert items[0].values['episode']=='001'
    with pytest.raises(ValueError,match='Duplicate'):
        service.create(items[0])


def test_sheet_ranges_and_validation(service):
    items=parse_rows([['episode','sequence','shot','cut_in','cut_out','fps'],
                      ['ep','sq','sh','1010','1050',str(service.shots.project_fps)]],{})
    assert service.request(items[0]).cut_out==1050
    items[0].values['cut_out']='1000'
    with pytest.raises(ValueError,match='cut_out'):
        service.request(items[0])
    items[0].values['cut_out']='1050.5'
    with pytest.raises(ValueError,match='integer'):
        service.request(items[0])


def test_reader_respects_gid_and_readonly_scope(service,monkeypatch,tmp_path):
    credential=tmp_path/'credential.json'
    credential.write_text('{}')
    calls={}
    sheet=SimpleNamespace(id=7,title='Shots',get_all_values=lambda:[['episode','sequence','shot'],['001','sq','sh']])
    class Book:
        def get_worksheet_by_id(self,gid):
            calls['gid']=gid
            return sheet
    class Client:
        def set_timeout(self,value): pass
        def open_by_key(self,value):
            calls['id']=value
            return Book()
    def account(**kwargs):
        calls.update(kwargs)
        return Client()
    monkeypatch.setitem(sys.modules,'gspread',SimpleNamespace(service_account=account))
    service.shots.project_config=SimpleNamespace(base={'google_sheets':{'shot_list_url':'https://docs.google.com/spreadsheets/d/abc/edit#gid=7'}})
    monkeypatch.setattr(service.shots,'credentials_path',lambda:credential)
    result=service.read()
    assert calls['gid']==7 and calls['id']=='abc'
    assert calls['scopes']==['https://www.googleapis.com/auth/spreadsheets.readonly']
    assert result[0].values['episode']=='001'


def test_dialog_creates_only_selected_new_row(service,monkeypatch):
    from PySide6 import QtCore,QtWidgets
    from smartlib.apps.shot_manager.shot_list_ui import ShotListDialog
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(ShotListDialog,'load',lambda self:None)
    dialog=ShotListDialog(service.shots)
    try:
        dialog.rows=rows(['ep','sq','one','',''],['ep','sq','two','',''])
        dialog.populate()
        dialog.table.item(0,0).setCheckState(QtCore.Qt.Checked)
        dialog.create_selected()
        assert [i.shot for i in service.shots.list_shots()]==['one']
        assert dialog.table.item(0,6).text()=='Existing'
        assert dialog.table.item(1,6).text()=='New'
    finally:
        dialog.close()


@pytest.mark.parametrize("value", ["1001-1120", "1001 - 1120", "1001〜1120", "1001–1120"])
def test_range_column_controls_frames(service, value):
    row = rows(['crowds', 'school', 'test01', '', ''])[0]
    row.values['range'] = value
    request = service.request(row)
    assert (request.cut_in, request.cut_out) == (1001, 1120)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_range_uses_240_frames_even_with_legacy_columns(service, value):
    row = rows(['ep', 'sq', 'sh', '', ''])[0]
    row.values.update(range=value, cut_in='1', cut_out='20')
    request = service.request(row)
    assert (request.cut_in, request.cut_out) == (1001, 1240)


@pytest.mark.parametrize("value", ["1001", "1001-", "1200-1001", "bad", "1001.5-1120"])
def test_invalid_range_does_not_fall_back(service, value):
    row = rows(['ep', 'sq', 'sh', '', ''])[0]
    row.values['range'] = value
    with pytest.raises(ValueError):
        service.create(row)
    assert not service.shots.list_shots()


def test_conflicting_columns_rejected(service):
    row = rows(['ep', 'sq', 'sh', '', ''])[0]
    row.values.update(range='1001-1120', cut_out='1240')
    with pytest.raises(ValueError, match='conflicts'):
        service.request(row)


def test_range_survives_creation(service):
    row = rows(['ep', 'sq', 'sh', '', ''])[0]
    row.values['range'] = '1010-1050'
    identity = service.create(row)
    path = service.shots.paths.artifact_file(service.shots.shot_root(identity), 'shot.json')
    assert read_json(path, {})['editorial']['duration'] == 41


def test_created_shot_is_counted_in_identity_only_sequence(service):
    from smartlib.apps.shot_manager.service import SequenceIdentity
    service.create(rows(['crowds', 'school', 'test01', '', ''])[0])
    data = service.shots.load_sequence(SequenceIdentity('crowds', 'school'))
    assert data['shots'] == [{'shot': 'test01'}]


def test_explicit_sequence_members_are_preserved(service):
    from smartlib.apps.shot_manager.service import SequenceIdentity
    from smartlib.core.metadata import write_json
    identity = SequenceIdentity('ep', 'sq')
    root = service.shots.paths.sequence_root('ep', 'sq')
    write_json(service.shots.paths.artifact_file(root, 'sequence.json'),
               {'episode': 'ep', 'sequence': 'sq', 'shots': [], 'status': 'hold'})
    data = service.shots.load_sequence(identity)
    assert data['shots'] == []
    assert data['status'] == 'hold'
