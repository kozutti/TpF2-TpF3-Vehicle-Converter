"""Classify DDS by shader usage and keep variants for shared source images."""
from pathlib import Path
import hashlib
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

from converter.adapters.tpf2_reader import cancelled, diagnostic, notify
from converter.parsers.lua import parse, dumps
from converter.resolver import walk

ROLE_BY_MAP = {'map_albedo': 'opaque_albedo', 'map_dirt': 'opaque_albedo', 'map_rust': 'opaque_albedo',
               'map_metal_gloss_ao': 'metal_gloss_ao',
               'map_normal': 'normal', 'map_dirt_normal': 'normal', 'map_rust_normal': 'normal',
               'map_albedo_opacity': 'transparent_albedo'}

DEFAULT_TEXTURE_WORKERS = 8
MAX_TEXTURE_WORKERS = 64
_worker_cancel = None


def validate_texture_workers(workers):
    if type(workers) is not int or not 1 <= workers <= MAX_TEXTURE_WORKERS:
        raise ValueError(f'テクスチャ並列数は1～{MAX_TEXTURE_WORKERS}を指定してください。')
    return workers


def _init_texture_worker(cancel):
    global _worker_cancel
    _worker_cancel = cancel


def _convert_texture(job, cancel=None):
    """Only DDS files are written in workers; material edits stay in the parent."""
    from converter.transforms.textures import convert_dds, inspect_dds
    source, target, role = job[:3]
    normal_layout = job[3] if len(job) > 3 else 'rgb'
    try:
        before = inspect_dds(source, allow_raw=True)
        result = convert_dds(source, target, role, cancel=cancel, normal_layout=normal_layout)
        diagnostics = result.get('diagnostics', [])
        if any(d.get('severity') == 'error' for d in diagnostics) or not target.is_file():
            return {'ok': False, 'diagnostics': diagnostics}
        return {'ok': True, 'diagnostics': diagnostics, 'before': before, 'after': inspect_dds(target)}
    except (ValueError, OSError) as exc:
        return {'ok': False, 'diagnostics': [diagnostic('TEXTURE_CONVERSION_FAILED', str(exc), str(source))]}


def _run_texture_job(job):
    return _convert_texture(job, _worker_cancel)


def _convert_jobs(jobs, workers, progress, cancel):
    results = [None] * len(jobs)
    completed = 0

    def check_cancel():
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')

    def record(index, result):
        nonlocal completed
        results[index] = result
        completed += 1
        notify(progress, 'textures', completed, len(jobs), f'{jobs[index][0].name}: {jobs[index][2]}')

    check_cancel()
    if not jobs:
        return results
    workers = min(workers, len(jobs))
    notify(progress, 'textures', 0, len(jobs), f'DDS変換: {len(jobs)}件、最大{workers}並列')
    if workers == 1:
        for index, job in enumerate(jobs):
            check_cancel()
            record(index, _convert_texture(job, cancel))
        check_cancel()
        return results

    # Spawn works with Windows, GUI worker threads, and the frozen executable.
    context = multiprocessing.get_context('spawn')
    stop = context.Event()
    # Windows allows at most 61 workers in one executor. Use two balanced
    # pools for 62-64, sharing cancellation and a single total job limit.
    sizes = [workers] if workers <= 61 else [workers // 2, workers - workers // 2]
    pools = []
    active = [0] * len(sizes)
    pending = {}
    next_index = 0
    try:
        for size in sizes:
            pools.append(ProcessPoolExecutor(max_workers=size, mp_context=context,
                                             initializer=_init_texture_worker, initargs=(stop,)))
        while next_index < len(jobs) or pending:
            check_cancel()
            # Keep the queue bounded so cancellation does not drain a long backlog.
            for pool_index, pool in enumerate(pools):
                while next_index < len(jobs) and active[pool_index] < sizes[pool_index]:
                    check_cancel()
                    pending[pool.submit(_run_texture_job, jobs[next_index])] = (next_index, pool_index)
                    active[pool_index] += 1
                    next_index += 1
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            check_cancel()
            for future in done:
                index, pool_index = pending.pop(future)
                active[pool_index] -= 1
                record(index, future.result())
        check_cancel()
    finally:
        stop.set()
        # Workers check the shared event inside the DDS block loops. Join before
        # staging cleanup so no child writes into a folder being removed.
        for pool in pools:
            pool.shutdown(wait=True, cancel_futures=True)
    return results

def texture_uses(data):
    for property_name, role in ROLE_BY_MAP.items():
        prop = data.get('params', {}).get(property_name, {})
        for key, value, location in walk(prop):
            if key == 'fileName' and isinstance(value, str) and value.lower().endswith('.dds'):
                yield prop, location, value, role

def local_texture(content, owner, reference):
    if '::' in reference:
        return None
    path = content / reference.lstrip('/') if reference.startswith('/') else owner.parent / reference
    path = path.resolve()
    return path if path.is_relative_to(content.resolve()) else None

def convert_package_textures(package, progress=None, cancel=None, workers=DEFAULT_TEXTURE_WORKERS):
    validate_texture_workers(workers)
    content = Path(package) / 'content'
    diagnostics, job_indices, jobs, prepared = [], {}, [], []
    materials = sorted(content.rglob('*.mtl'))
    for path in materials:
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        data = parse(path.read_text('utf-8'), str(path))
        uses = []
        for prop, location, reference, role in texture_uses(data):
            source = local_texture(content, path, reference)
            if source is None or not source.is_file():
                continue  # Reference validator supplies missing-local diagnostics.
            declared_rg = role == 'normal' and any(
                isinstance(sampler, dict) and sampler.get('fileName') == reference and sampler.get('redGreen') is True
                for _, sampler, _ in walk(prop))
            declared_rg = declared_rg or (role == 'normal' and prop.get('redGreen') is True)
            normal_layout = 'rgb_with_rg_fallback' if declared_rg else 'rgb'
            key = (str(source), role, normal_layout)
            if key not in job_indices:
                # Each source/role owns exactly one output, even across materials.
                identity = source.relative_to(content).as_posix() + ':' + role
                if declared_rg:
                    identity += ':declared_rg'
                token = hashlib.sha256(identity.encode()).hexdigest()[:12]
                target = content / '_assets' / 'converted_textures' / (token + '_' + role + '.dds')
                job_indices[key] = len(jobs)
                jobs.append((source, target, role, normal_layout))
            uses.append((prop, reference, job_indices[key]))
        prepared.append((path, data, uses))

    results = _convert_jobs(jobs, workers, progress, cancel)
    reported = set()
    # Preserve diagnostic/material order regardless of worker completion order.
    for path, data, uses in prepared:
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        changed = False
        for prop, reference, index in uses:
            result = results[index]
            source, target, role = jobs[index][:3]
            target_reference = '/' + target.relative_to(content).as_posix()
            if index not in reported:
                reported.add(index)
                diagnostics.extend(result['diagnostics'])
                if result['ok']:
                    before, after = result['before'], result['after']
                    diagnostics.append(diagnostic('TEXTURE_FORMAT_CONVERTED',
                        f'{role}: {before.get("format")} → {after.get("format")}',
                        path.relative_to(content).as_posix(), severity='info',
                        rule_id='dds_shader_role_v2', source_texture=reference,
                        target_texture=target_reference, before=before, after=after))
            if result['ok']:
                for _, sampler, _ in walk(prop):
                    if isinstance(sampler, dict) and sampler.get('fileName') == reference:
                        sampler['fileName'] = target_reference
                if prop.get('fileName') == reference:
                    prop['fileName'] = target_reference
                changed = True
        if changed:
            path.write_text(dumps(data), 'utf-8')
    # DDS files that no known slot converted (roll-sign textures and the like) are copied as they are; TPF3 warns about a
    # compressed texture with a single level, so every DDS of the package gets its missing mip levels.
    from converter.transforms.textures import complete_mips_in_place
    for path in sorted(content.rglob('*.dds')):
        if cancelled(cancel):
            raise InterruptedError('変換を取り消しました。')
        levels = complete_mips_in_place(path, cancel)
        if levels:
            diagnostics.append(diagnostic('TEXTURE_MIPMAPS_GENERATED',
                f'mipmapが1段しかなかったため、{levels}段まで生成しました（縮小して再圧縮。最上位の画像は変えていません）。',
                path.relative_to(content).as_posix(), severity='info'))
    return diagnostics

def validate_texture_uses(content, parsed):
    from converter.transforms.textures import inspect_dds
    diagnostics, seen = [], set()
    for relative, data in parsed.items():
        if not relative.endswith('.mtl'):
            continue
        for prop, location, ref, role in texture_uses(data):
            texture = local_texture(content, content / relative, ref)
            if texture is None or not texture.is_file() or (str(texture), role) in seen:
                continue
            seen.add((str(texture), role))
            try:
                info = inspect_dds(texture)
                kept = role == 'normal' and info.get('format') in {'BC1', 'BC2', 'BC3'} and '/converted_textures/' in ref.replace('\\', '/')
                if (role in {'normal', 'opaque_albedo', 'metal_gloss_ao'} and info.get('format') == 'BC7') or kept:
                    diagnostics.append(diagnostic('TEXTURE_FORMAT_NOT_RECOMMENDED',
                        f'{role} のDDS形式は{info.get("format")}です。TPF3の推奨は{"BC5" if role == "normal" else "BC1"}ですが、そのまま使えます。',
                        relative, severity='warning', location=location, texture=ref))
                    continue
                allowed = {'BC5'} if role == 'normal' else {'BC1'} if role in {'opaque_albedo', 'metal_gloss_ao'} else {'BC1', 'BC2', 'BC3', 'BC7'}
                if info.get('format') not in allowed:
                    diagnostics.append(diagnostic('TEXTURE_FORMAT_INVALID',
                        f'{role} のDDS形式 {info.get("format")} はTPF3の要求 {sorted(allowed)} と一致しません。',
                        relative, location=location, texture=ref))
            except (ValueError, OSError) as exc:
                diagnostics.append(diagnostic('DDS_INVALID', str(exc), relative, texture=ref))
    return diagnostics
