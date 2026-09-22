#!/usr/bin/env bash
#SBATCH --job-name=nwm-cnrfc-buffer20km-parameters-routing-extraction
#SBATCH --partition=shared-128
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --tmp=120000
#SBATCH --time=12:00:00
set -euo pipefail
root=${SLURM_SUBMIT_DIR:?}
scratch="/scratch/${SLURM_JOB_USER:?}/job_${SLURM_JOB_ID:?}"
python=/home/mpan/local/miniforge3/envs/hydro-ops/bin/python
export PATH="/home/mpan/local/miniforge3/envs/hydro-ops/bin:/home/mpan/local/miniforge3/bin:$PATH"
export PYTHONPATH="$root/src"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
target="$root/nwm/static/domains/cnrfc/parameters"
result="$root/nwm/status/cnrfc/parameter-extraction/job_${SLURM_JOB_ID}"
mkdir -p "$scratch" "$result"
test ! -e "$target"
# Serialize any competing extraction attempts for this domain.
exec 9>"$root/nwm/static/domains/cnrfc/parameter-extraction.lock"
flock -n 9
test ! -e "$target"
"$python" "$root/bin/subset_nwm_domain.py" \
    --domain-dir "$root/nwm/static/operational/nwm.v3.1.6/domain" \
    --domain-masks "$root/nwm/static/domains/cnrfc/masks/cnrfc_masks.nc" \
    --output-dir "$scratch/cnrfc-parameters" --execute \
    > "$result/extraction.log" 2>&1
"$python" - "$root" "$scratch/cnrfc-parameters" "$target" "$result" <<'PY'
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from hydro_ops.nwm_masks import load_masks

root, source, target, result = map(Path, sys.argv[1:])
mask_path = root / 'nwm/static/domains/cnrfc/masks/cnrfc_masks.nc'
window, masks, lat, lon = load_masks(mask_path)
manifest = json.loads((source / 'subset_manifest.json').read_text())
assert manifest['network']['topology_validation'] == 'passed'
with Dataset(source / 'wrfinput_CONUS.nc') as data:
    np.testing.assert_array_equal(data['XLAND'][0] == 1, masks['model_mask'])
    np.testing.assert_allclose(data['XLAT'][0], lat, atol=1e-5, rtol=0)
    np.testing.assert_allclose(data['XLONG'][0], lon, atol=1e-5, rtol=0)
with Dataset(source / 'Fulldom_CONUS_FullRouting.nc') as data:
    assert (len(data.dimensions['y']), len(data.dimensions['x'])) == window.refined(4).shape
shutil.copy2(mask_path, source / 'domain_masks.nc')
manifest['reporting_boundary'] = str(root / 'nwm/static/domains/cnrfc/boundaries/cnrfc_domain_boundaries.gpkg')
manifest['reporting_boundary_layer'] = 'cnrfc_union'
manifest['reporting_policy'] = 'Official CNRFC union forecast basins, including Mexico extensions, is the presentation region; only the additional 20-km buffer is supporting domain.'
manifest['routing_acceptance_policy'] = 'Structural consistency required; imperfect drainage closure accepted. Boundary diagnostics retained, not a perfect-closure guarantee.'
manifest['extraction_status'] = 'passed_not_model_smoke_tested'
(source / 'subset_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(block)
    return h.hexdigest()

stage = target.with_name(f'.parameters-job-{Path(result).name}')
if stage.exists() or target.exists():
    raise FileExistsError('Refusing to replace another extraction')
shutil.copytree(source, stage)
files = {}
for p in source.iterdir():
    if p.is_file():
        checksum = digest(p)
        assert checksum == digest(stage / p.name), p.name
        files[p.name] = {'bytes': p.stat().st_size, 'sha256': checksum}
stage.rename(target)
report = {'status': 'passed', 'output': str(target), 'model_grid_shape': window.shape,
          'routing_grid_shape': window.refined(4).shape, 'files': files,
          'network': manifest['network'], 'model_smoke_tested': False,
          'routing_acceptance_policy': manifest['routing_acceptance_policy']}
(result / 'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
PY
