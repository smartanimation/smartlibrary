"""Version scene Data first, then rebuild Layout only from fixed Data."""
from smartlib.core.metadata import write_json


def capture(service, identity, request, cmds):
    from .shot_publish_sources import token
    options = request['scene_options']
    selections = options['targets']
    category = options['category']
    if not selections or len(selections) != len(set(selections)):
        raise ValueError('Select unique Layout sources')
    rows = []
    if category == 'placements':
        from .placement import list_placement_locators, _collect_placement_metadata
        from .placement_usd import capture_placements
        locators = list_placement_locators()
        by_name = {token(row.name): row for row in locators}
        if len(by_name) != len(locators):
            raise ValueError('Placement names produce duplicate targets')
        timing = service.plan(identity, [], frame_range=request['frame_range'], replace_assets=True)
        capture_placements(timing, cmds, locators)  # Validate scene units and FPS even for unassigned markers.
        import math
        previous = cmds.currentTime(query=True)
        try:
            for name in selections:
                locator = by_name[name]
                frames = [float(request['frame_range'][0])]
                if locator.motion == 'CURVE':
                    end = float(request['frame_range'][1])
                    frames += [frames[0] + i for i in range(1, math.ceil(end - frames[0]))]
                    if end > frames[0]:
                        frames.append(end)
                samples = []
                for frame in frames:
                    cmds.currentTime(frame, edit=True)
                    samples.append(dict(frame=frame, matrix=list(cmds.xform(locator.node, query=True, worldSpace=True, matrix=True))))
                payload = dict(schema='smartpipeline.placement_samples.v1', locator=locator.node,
                    parent=locator.parent, member=locator.member,
                    marker=dict(mode=locator.motion, samples=samples), usd_placements=[])
                cmds.currentTime(frames[0], edit=True)
                metadata, members = _collect_placement_metadata()
                payload['placements'] = [row for row in metadata['placements'] if row['locator'] == locator.name]
                payload['placement_members'] = [row for row in members['placements'] if row['locator'] == locator.name]
                if locator.member:
                    timing['rows'] = [dict(target=locator.member, geometry_source='asset')]
                    captured = capture_placements(timing, cmds, locators)
                    from .assembly_placement import capture as capture_assembly
                    binding = capture_assembly(cmds, locator, service.shots.load_cast(identity).get('cast', {}), frames)
                    item = dict(path='/Shot/Assets/' + (binding['cast'] if binding else locator.member),
                                data=captured[locator.member])
                    if binding:
                        item['assembly'] = binding
                    payload['usd_placements'] = [item]
                rows.append(_publish_data(service, identity, name, 'placement', payload, request))
        finally:
            cmds.currentTime(previous, edit=True)
    elif category == 'set_dress':
        from .set_dress import load_package_from_scene, layer_package
        package, _ = load_package_from_scene(cmds)
        if package is None:
            raise ValueError('No Smart Set Dress layers in saved scene')
        layers = [layer for layer in package.layers if layer.scope == 'shot']
        if len({token(layer.name) for layer in layers}) != len(layers):
            raise ValueError('Set Dress names produce duplicate targets')
        if not set(selections) <= {token(layer.name) for layer in layers}:
            raise ValueError('Selected Set Dress layer no longer exists')
        # Preserve Smart Set Dress order: top layer wins.
        for layer in layers:
            name = token(layer.name)
            if name not in selections:
                continue
            payload = layer_package(package, layer).to_dict()
            row = _publish_data(service, identity, name, 'setdress', payload, request)
            row['node_map'] = options.get('node_map', {})
            row['layer_order'] = layers.index(layer)
            rows.append(row)
    else:
        raise ValueError('Invalid Layout category')
    return service.plan(identity, rows, frame_range=request['frame_range'])


def _publish_data(service, identity, name, category, payload, request):
    root = service.paths.shot_data_dir(*service._identity(identity), category, name, 'main')
    version, directory = service._reserve(root)
    filename = 'placements.json' if category == 'placement' else name + '.setdress.json'
    source = service._file(directory, filename)
    write_json(source, payload)
    files = {category: filename}
    if category == 'placement':
        files = dict(placements=filename, placement_members='placement_members.json')
        write_json(service._file(directory, files['placement_members']), dict(placements=payload['placement_members']))
    write_json(service._file(directory, 'data.json'), dict(data_type=category, target=name,
        subset='main', version=version, files=files, source_scene=request['scene_input']))
    write_json(service._file(root, 'latest.json'), dict(version=version, path=source.relative_to(root).as_posix()))
    return dict(kind='layout', target=category + '_' + name, source=str(source),
                layout_type=category, layout_name=name)
