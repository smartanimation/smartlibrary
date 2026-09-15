"""Bake a selected skeleton into an isolated FBX export hierarchy."""
from pathlib import Path
import re
from smartlib.apps.asset_manager.environment_pack import digest


def inspect_scene(root, anchor):
    import maya.cmds as c
    import maya.api.OpenMaya as om
    if c.file(q=True,modified=True): raise ValueError('Save the current motion Work scene first.')
    if not c.objExists(root) or c.nodeType(root) != 'joint': raise ValueError('Select the deformation skeleton root joint.')
    if not c.objExists(anchor): raise ValueError('Select a placement anchor transform or joint.')
    roots=c.ls(root,long=True)
    if len(roots)!=1: raise ValueError('Skeleton root is ambiguous; use a full DAG path.')
    joints=[roots[0]] + sorted(c.listRelatives(roots[0],allDescendents=True,type='joint',fullPath=True) or [],key=lambda n:n.count('|'))
    names=[j.rsplit('|',1)[-1].split(':')[-1] for j in joints]
    if len(set(names))!=len(names): raise ValueError('Skeleton bone names must be unique after removing namespaces.')
    refs=[]
    for raw in c.file(q=True,reference=True) or []:
        path=Path(re.sub(r'\{\d+\}$','',raw))
        if not path.is_file(): raise FileNotFoundError(path)
        refs.append(dict(path=str(path),sha256=digest(path),version=path.parent.name if re.fullmatch(r'v[0-9]+',path.parent.name) else None))
    return dict(skeleton_root=roots[0],placement_anchor=anchor,
        frame_range=[int(c.playbackOptions(q=True,minTime=True)),int(c.playbackOptions(q=True,maxTime=True))],
        fps=om.MTime(1,om.MTime.kSeconds).asUnits(om.MTime.uiUnit()),
        linear_unit=c.currentUnit(q=True,linear=True),up_axis=c.upAxis(q=True,axis=True).upper(),
        rig_references=refs,bone_names=names)


def export_fbx(target, metadata):
    import maya.cmds as c
    import maya.mel as mel
    root=metadata['skeleton_root']
    original=c.ls(selection=True,long=True) or []
    time=c.currentTime(q=True);modified=c.file(q=True,modified=True)
    namespace=c.namespace(add='_motionExport', parent=':')
    created=[]
    pushed=False
    try:
        joints=[root]+sorted(c.listRelatives(root,allDescendents=True,type='joint',fullPath=True) or [],key=lambda n:n.count('|'))
        hierarchy=set(joints)
        for joint in joints:
            parent=joint.rsplit('|',1)[0]
            while parent.startswith(root+'|'):
                hierarchy.add(parent);parent=parent.rsplit('|',1)[0]
        hierarchy=sorted(hierarchy,key=lambda n:n.count('|'))
        mapping={}
        for node in hierarchy:
            parent=node.rsplit('|',1)[0]
            duplicate=c.createNode('joint' if c.nodeType(node)=='joint' else 'transform',
                name=namespace+':'+node.rsplit('|',1)[-1].split(':')[-1],
                **({'parent':mapping[parent]} if parent in mapping else {}))
            mapping[node]=c.ls(duplicate,long=True)[0];created.append(mapping[node])
            for attr in ('rotateOrder','segmentScaleCompensate'):
                if c.attributeQuery(attr,node=node,exists=True):c.setAttr(duplicate+'.'+attr,c.getAttr(node+'.'+attr))
            if c.nodeType(node)=='joint' and parent in mapping:
                if c.listConnections(node+'.inverseScale',source=True,destination=False):
                    c.connectAttr(mapping[parent]+'.scale',duplicate+'.inverseScale',force=True)
        start,end=metadata['frame_range']
        for frame in range(int(start),int(end)+1):
            c.currentTime(frame,edit=True)
            for node in hierarchy:
                duplicate=mapping[node]
                attrs=['translate','rotate','scale','shear','rotateAxis']
                if c.nodeType(node)=='joint':attrs.append('jointOrient')
                for attr in attrs:
                    c.setAttr(duplicate+'.'+attr,*c.getAttr(node+'.'+attr)[0])
                if node==root:
                    c.xform(duplicate,worldSpace=True,matrix=c.xform(node,q=True,worldSpace=True,matrix=True))
                c.setKeyframe(duplicate,attribute=attrs,time=frame)
        c.filterCurve(created)
        c.currentTime(start,edit=True)
        anchor_matrix=c.xform(metadata['placement_anchor'],q=True,worldSpace=True,matrix=True)
        c.loadPlugin('fbxmaya',quiet=True)
        mel.eval('FBXPushSettings;');pushed=True
        mel.eval('FBXResetExport;')
        mel.eval('FBXExportInputConnections -v false;')
        mel.eval('FBXExportConstraints -v false;')
        mel.eval('FBXExportCameras -v false;')
        mel.eval('FBXExportLights -v false;')
        mel.eval('FBXExportBakeComplexAnimation -v true;')
        mel.eval(f'FBXExportBakeComplexStart -v {start}; FBXExportBakeComplexEnd -v {end}; FBXExportBakeComplexStep -v 1;')
        c.select(created[0],replace=True)
        escaped=str(target).replace('\\','/').replace('"','\\"')
        mel.eval('FBXExport -f "'+escaped+'" -s;')
        return dict(joint_count=len(joints),sample_count=int(end-start)+1,placement_anchor_matrix=anchor_matrix,
                    content='baked_skeleton',geometry='agent_definition_from_assets')
    finally:
        if pushed:mel.eval('FBXPopSettings;')
        if created and c.objExists(created[0]):c.delete(created[0])
        c.namespace(removeNamespace=namespace,mergeNamespaceWithRoot=True)
        c.currentTime(time,edit=True)
        c.select(original,replace=True) if original else c.select(clear=True)
        c.file(modified=modified)
