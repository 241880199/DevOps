"""核对真实 E8 基线，并保存个人间接包含往返实验的完整证据。"""
import json
import sys
from pathlib import Path

from lab import buildchecker, commit, fixture, run, save, scan


def findings_set(report):
    """把发现转成集合，以比较具体类型、目标和依赖而不是只比较数量。"""
    return {(item['type'], item['target'], item['dependency']) for item in report['findings']}


def main():
    """校验标准 E8，再在独立新副本验证间接包含的新增、复用与移除。"""
    original, output = map(Path, sys.argv[1:])
    baseline = json.loads((original / 'baseline.json').read_text())
    unchanged = json.loads((original / 'incremental.json').read_text())
    expected = {('MISSING', 'main.o', 'config.h'), ('REDUNDANT', 'main.o', 'unused.h')}
    assert findings_set(baseline) == expected
    assert baseline['actual'] == {'main.o': ['config.h', 'main.c'], 'helper.o': ['helper.c']}
    assert baseline['baseline_source'].startswith('BuildChecker actual-graph.json')
    assert unchanged['reused'] == ['main.o', 'helper.o'] and unchanged['refreshed'] == []
    assert unchanged['added'] == [] and unchanged['resolved'] == []
    for field in ('actual', 'declared', 'findings'):
        assert unchanged[field] == baseline[field]
    assert run(['git', 'status', '--porcelain'], original / 'project') == ''
    save(original / 'verification.json', {'passed': True, 'expected_findings': sorted(expected), 'reused': unchanged['reused']})
    if output.exists():
        raise ValueError('扩展输出目录必须不存在，避免覆盖旧证据')
    output.mkdir(parents=True)
    project = output / 'project'
    fixture(project)
    initial = scan(project)
    buildchecker(project, output / 'baseline-buildchecker')
    edges = json.loads((output / 'baseline-buildchecker/actual-graph.json').read_text())
    initial['actual'] = {target: sorted({edge['dependency'] for edge in edges if edge['target'] == target}) for target in ('main.o', 'helper.o')}
    initial['baseline_source'] = 'BuildChecker actual-graph.json projected to object targets'
    save(output / 'baseline.json', initial)
    header = (project / 'config.h').read_text()
    (project / 'config.h').write_text('#include "extra.h"\n' + header, encoding='utf-8')
    commit(project, 'E8 个人扩展：增加间接包含')
    added = scan(project, initial)
    full = scan(project)
    save(output / 'added-incremental.json', added)
    save(output / 'added-full.json', full)
    assert run(['git', 'diff', '--name-only', initial['commit'], added['commit']], project).strip() == 'config.h'
    assert findings_set(added) == expected | {('MISSING', 'main.o', 'extra.h')}
    assert added['actual'] == {'main.o': ['config.h', 'extra.h', 'main.c'], 'helper.o': ['helper.c']}
    assert added['reused'] == [] and set(added['refreshed']) == {'main.o', 'helper.o'}
    assert added['added'] == [['MISSING', 'main.o', 'extra.h']] and added['resolved'] == []
    for field in ('actual', 'declared', 'findings'):
        assert added[field] == full[field]
    buildchecker(project, output / 'added-buildchecker')
    stable = scan(project, added)
    save(output / 'unchanged.json', stable)
    assert stable['reused'] == ['main.o', 'helper.o'] and stable['refreshed'] == []
    assert stable['actual'] == added['actual'] and stable['findings'] == added['findings']
    (project / 'config.h').write_text(header, encoding='utf-8')
    commit(project, 'E8 个人扩展：移除间接包含')
    removed = scan(project, added)
    removed_full = scan(project)
    save(output / 'removed-incremental.json', removed)
    save(output / 'removed-full.json', removed_full)
    assert removed['actual'] == initial['actual'] and findings_set(removed) == expected
    assert removed['reused'] == [] and set(removed['refreshed']) == {'main.o', 'helper.o'}
    assert removed['added'] == [] and removed['resolved'] == [['MISSING', 'main.o', 'extra.h']]
    for field in ('actual', 'declared', 'findings'):
        assert removed[field] == removed_full[field]
    buildchecker(project, output / 'removed-buildchecker')
    assert run(['git', 'status', '--porcelain'], project) == ''
    save(output / 'verification.json', {'passed': True, 'only_changed_input': 'config.h', 'added': added['added'], 'resolved': removed['resolved'], 'dynamic_crosschecks': 3})
    print('E8 标准基线及个人间接包含往返校验全部通过')


if __name__ == '__main__':
    main()
