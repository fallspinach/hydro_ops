"""Stage, validate and transactionally publish one midnight CNRFC NRT replay."""
# Model scheduling APIs deliberately use naive UTC.
# ruff: noqa: DTZ001, DTZ007
import argparse
import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from run_cnrfc_production import forcing_identity, run

from hydro_ops.wrf_hydro.nrt_cycle import (
    checkpoints,
    choose_start,
    discover_plan,
    prepare_publication,
    publish_transaction,
    retention_candidates,
)
from hydro_ops.wrf_hydro.nrt_recovery import reuse_prefix, snapshot_forcing
from hydro_ops.wrf_hydro.production import restart_check, restart_names, write_json


def require_corrected_replay(acceptance):
    if (acceptance.get('status') != 'complete' or not acceptance.get('test')
            or acceptance.get('daily_checkpoint_continuation') != 'passed'
            or acceptance.get('rst_typ') != 1):
        raise ValueError('Corrected daily replay acceptance gate has not passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', choices=('cnrfc', 'b120', 'yampa'), default='cnrfc')
    parser.add_argument('--test', action='store_true', help='Publish only to an isolated test mirror')
    parser.add_argument('--end-date')
    parser.add_argument('--rewind-days', type=int, default=11)
    parser.add_argument('--refresh-from-retro', action='store_true',
                        help='One-time corrected NRT history from the March 2 retro restart')
    parser.add_argument('--acceptance-report', type=Path,
                        help='Passed daily-checkpoint/replay test required for historical refresh')
    parser.add_argument('--resume-workspace', type=Path,
                        help='Reuse an unpublished failed refresh; snapshot and rerun only its tail')
    parser.add_argument('--snapshot-forcing', action='store_true',
                        help='Pin validated forcing versions for the entire replay window')
    parser.add_argument('--migration-test', action='store_true', help='Isolated post-promotion replay validation')
    args = parser.parse_args()
    if args.resume_workspace and not args.refresh_from_retro:
        parser.error('--resume-workspace requires --refresh-from-retro')
    root = Path(__file__).resolve().parents[1]
    domain = args.domain
    if domain == 'cnrfc':
        from hydro_ops.cnrfc_domain import require_available
        require_available(root, migration_test=args.test and args.migration_test)
    else:
        if args.refresh_from_retro or args.migration_test:
            parser.error('Historical CNRFC refresh/migration flags require --domain cnrfc')
        if not args.snapshot_forcing:
            parser.error('Subdomain daily replay requires --snapshot-forcing')
        from run_b120_production import approved
        approved(root, domain)
        promotion = root / f'nwm/status/{domain}/nrt-promotion/promotion.json'
        if json.loads(promotion.read_text()).get('status') != 'complete':
            raise ValueError('Complete NRT promotion required before daily replay')
    configuration = json.loads((root / f'config/{domain}_nrt_daily.json').read_text())
    if domain != 'cnrfc' and not args.test:
        from hydro_ops.wrf_hydro.nrt_schedule import require_daily_acceptance
        require_daily_acceptance(root, domain, configuration)
    stream = root / f'nwm/runs/{domain}/nrt'
    stream.mkdir(parents=True, exist_ok=True)
    with (stream / 'production.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        end = datetime.strptime(args.end_date, '%Y-%m-%d') if args.end_date else None
        if args.refresh_from_retro:
            if args.test or args.acceptance_report is None:
                raise ValueError('Refresh requires a passed test report and cannot be combined with --test')
            acceptance = json.loads(args.acceptance_report.read_text())
            require_corrected_replay(acceptance)
            plan = discover_plan(root, end, args.rewind_days)
            start = datetime(2026, 3, 2)
            stop = datetime.fromisoformat(plan['end'])
            if stop <= start:
                raise ValueError('Refresh end must follow the inherited retro restart')
            plan.update(start=str(start), initial_restart_dir=str(root / 'nwm/restarts/cnrfc/retro/2026/03'),
                        days=(stop-start).days, initialization='Inherited legacy retro state; corrected NRT replay')
        elif args.test and end is not None:
            available = checkpoints(root / f'nwm/restarts/{domain}/nrt')
            start = choose_start(end, available, args.rewind_days)
            plan = {'start': str(start), 'end': str(end), 'initial_restart_dir': str(available[start]),
                    'rewind_days': args.rewind_days, 'days': (end-start).days}
        else:
            plan = discover_plan(root, end, args.rewind_days, domain)
        start, end = [datetime.fromisoformat(plan[k]) for k in ('start', 'end')]
        job = os.environ['SLURM_JOB_ID']
        if not job.isdecimal():
            raise ValueError('Expected numeric SLURM job ID')
        workspace = stream / 'cycles' / f'job-{job}'
        workspace.mkdir(exist_ok=False, parents=True)
        report = plan | {'status': 'running', 'domain': domain, 'test': args.test, 'job_id': job, 'rst_typ': 1,
                         'historical_nrt_refresh': args.refresh_from_retro}
        write_json(workspace / 'cycle.json', report)
        gate = root / ('nwm/outputs/cnrfc/retro/tests/'
                       'archive_recovery_warmstart_19790201_48h/job_4642075/acceptance.json')
        if configuration.get('archive_gate'):
            gate = root / configuration['archive_gate']
        report['mask_sha256'] = json.loads(gate.read_text()).get('mask_sha256')
        try:
            initial = Path(plan['initial_restart_dir'])
            cursor = start
            frozen_forcing = None
            if args.resume_workspace:
                prior = args.resume_workspace.resolve()
                if prior.parent != workspace.parent.resolve() or prior == workspace:
                    raise ValueError('Resume source must be a different CNRFC cycle workspace')
                cursor, reused = reuse_prefix(prior, workspace, start, end)
                initial = workspace / 'restarts' / cursor.strftime('%Y/%m')
                restart_check([initial / n for n in restart_names(cursor)], cursor)
                report['reused_prefix'] = reused
            if args.resume_workspace or args.snapshot_forcing:
                frozen_forcing = workspace / 'forcing-snapshot'
                snapshot = snapshot_forcing(root / f'forcing/outputs/{domain}/nrt/hourly',
                                            frozen_forcing, cursor, end)
                report.update(forcing_snapshot=str(frozen_forcing),
                              snapshot_source_identities=snapshot)
                write_json(workspace / 'cycle.json', report)
            while cursor < end:
                stop = min(end, datetime(cursor.year + 1, 1, 1))
                run(root, cursor.year, 'nrt_continuation_v1', gate, stop.strftime('%Y-%m-%d'),
                    'nrt', cursor.strftime('%Y-%m-%d'), initial,
                    cycle_workspace=workspace, stream_lock=lock, forcing_root=frozen_forcing,
                    domain=domain, continuation_end=end)
                initial = workspace / 'restarts' / stop.strftime('%Y/%m')
                cursor = stop
            # Recheck every staged segment before any canonical activation.
            for path in (workspace / 'runs').glob('*/*/accepted.json'):
                segment = json.loads(path.read_text())
                if not segment.get('passed'):
                    raise ValueError('Unaccepted staged segment')
                if any(forcing_identity(Path(p)) != value
                       for p, value in segment['forcing_identities'].items()):
                    raise ValueError('Forcing changed since segment acceptance')
            pair = [initial / n for n in restart_names(end)]
            restart_check(pair, end)
            if frozen_forcing is not None:
                report['source_revisions_after_snapshot'] = [
                    p for p, identity in snapshot.items() if forcing_identity(Path(p)) != identity]
            if args.test and (end-start).days >= 2:
                resume_start = end - timedelta(days=1)
                resume = workspace / 'resume-check'
                run(root, resume_start.year, 'nrt_continuation_v1', gate, end.strftime('%Y-%m-%d'),
                    'nrt', resume_start.strftime('%Y-%m-%d'),
                    workspace / 'restarts' / resume_start.strftime('%Y/%m'),
                    cycle_workspace=resume, stream_lock=lock, forcing_root=frozen_forcing,
                    domain=domain, continuation_end=end)
                for name, variables in zip(restart_names(end),
                        [('SMC', 'SH2O', 'SOIL_T', 'SNEQV'), ('hlink', 'qlink1', 'qlink2', 'z_gwsubbas')]):
                    with Dataset(initial / name) as a, Dataset(resume / 'restarts' / end.strftime('%Y/%m') / name) as b:
                        for variable in variables:
                            x, y = np.ma.asarray(a[variable][:]), np.ma.asarray(b[variable][:])
                            np.testing.assert_array_equal(np.ma.getmaskarray(x), np.ma.getmaskarray(y))
                            np.testing.assert_allclose(x.compressed(), y.compressed(), rtol=1e-5, atol=1e-6)
                report['daily_checkpoint_continuation'] = 'passed'
            output_root = root / f'nwm/outputs/{domain}/nrt'
            restart_root = root / f'nwm/restarts/{domain}/nrt'
            if args.test:
                # Mirror only affected existing files using hardlinks, never edit
                # their contents; activation replaces test directory entries only.
                for kind, canonical in [('outputs', output_root), ('restarts', restart_root)]:
                    for source in (workspace / kind).rglob('*'):
                        if not source.is_file():
                            continue
                        relative = source.relative_to(workspace / kind)
                        old = canonical / relative
                        if old.exists():
                            target = workspace / 'test-current' / kind / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            os.link(old, target)
                output_root = workspace / 'test-current/outputs'
                restart_root = workspace / 'test-current/restarts'
            pairs = prepare_publication(workspace, output_root, restart_root)
            latest = root / f'nwm/status/{domain}/nrt-daily/latest.json'
            journal = workspace / 'publication/transaction.json'
            if not args.test:
                write_json(latest, report | {'status': 'publishing', 'journal': str(journal)})
            transaction = publish_transaction(pairs, journal)
            restart_check([restart_root / end.strftime('%Y/%m') / n for n in restart_names(end)], end)
            report.update(status='complete', published_files=len(transaction['files']),
                          checkpoint_count=(end-start).days, outputs=str(output_root),
                          restarts=str(restart_root), journal=str(journal),
                          retention_candidates=retention_candidates(restart_root, end),
                          retention_action='plan_only_pending_operational_acceptance')
            if not args.test:
                write_json(latest, report)
        except BaseException as error:
            report.update(status='failed', error=str(error))
            raise
        finally:
            write_json(workspace / 'cycle.json', report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
