import string
import click
import tempfile
import pathlib
import pandas as pd
import gufe
import json
from openff.units import unit
import openfe
from openfe.protocols.openmm_md.plain_md_methods import PlainMDProtocol
from rdkit import Chem


def get_settings():
    """
    Utility method for getting MDProtocol settings.

    These settings mostly follow defaults but use very short
    simulation times to avoid being too much of a burden on users' machines.
    """
    settings = openfe.protocols.openmm_md.plain_md_methods.PlainMDProtocol.default_settings()
    settings.simulation_settings.equilibration_length_nvt = 1 * unit.picosecond
    settings.simulation_settings.equilibration_length = 1 * unit.picosecond
    settings.simulation_settings.production_length = 500 * unit.picosecond
    settings.solvation_settings.box_shape = 'dodecahedron'
    settings.output_settings.checkpoint_interval = 100 * unit.picosecond
    settings.forcefield_settings.nonbonded_cutoff = 0.9 * unit.nanometer
    settings.engine_settings.compute_platform = 'cuda'
    return settings


def get_performance(dagres, protocol):
    """
    Get the final ns/day performance

    Parameters
    ----------
    dagres : openfe.ProtocolDAGResult
      The Protocol DAG result.
    protocol : openfe.Protocol
      The Protocol we ran.
    """
    protocol_results = protocol.gather([dagres])
    # hack to get the file path
    pdb_filename = protocol_results.get_pdb_filename()[0]
    filepath = pdb_filename.resolve().parent
    log = filepath / 'simulation.log'
    df = pd.read_csv(log)
    speed = df['Speed (ns/day)'].values
    return speed[-1]


def run_md(dag, protocol):
    """
    Run a DAG and check it was ok.

    Parameters
    ----------
    dag : openfe.ProtocolDAG
      A ProtocolDAG to execute.
    protocol : openfe.Protocol
      The Protocol we are running.

    Raises
    ------
    AssertionError
      If any of the simulation Units failed.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = pathlib.Path(tmpdir)
        dagres = gufe.protocols.execute_DAG(
            dag,
            shared_basedir=workdir,
            scratch_basedir=workdir,
            keep_shared=True,
            raise_error=True,
            n_retries=0,
        )

        if not dagres.ok():
            return 'NaN'
        else:
            return get_performance(dagres, protocol)


def run_inputs(pdb, cofactors, edge):
    """
    Validate input files by running a short MD simulation

    Parameters
    ----------
    pdb : pathlib.Path
      A Path to a protein PDB file.
    cofactors : Optional[pathlib.Path]
      A Path to an SDF file containing the system's cofactors.
    edge : Optional[pathlib.Path]
      A Path to a JSON serialized AtomMapping. ComponentA will
      be used as part of the simulation.
    """
    # Create the solvent and protein components
    solv = openfe.SolventComponent()
    prot = openfe.ProteinComponent.from_pdb_file(str(pdb))

    # Store there in a components dictionary
    components_dict = {
        'protein': prot,
        'solvent': solv,
    }

    # If we have cofactors, populate them and store them based on
    # an single letter index (we assume no more than len(alphabet) cofactors)
    if cofactors is not None:
        cofactors = [
            openfe.SmallMoleculeComponent(m)
            for m in Chem.SDMolSupplier(str(cofactors), removeHs=False)
        ]

        for cofactor, entry in zip(cofactors, string.ascii_lowercase):
            components_dict[entry] = cofactor

    if edge is not None:
        mapping = openfe.LigandAtomMapping.from_json(edge)
        components_dict['ligand'] = mapping.componentA

    # Create the ChemicalSystem
    system = openfe.ChemicalSystem(components_dict)

    # Get the settings and create the protocol
    settings = get_settings()
    protocol = PlainMDProtocol(settings=settings)

    # Now create the DAG and run it
    dag = protocol.create(stateA=system, stateB=system, mapping=None)
    return run_md(dag, protocol)


@click.command
@click.option(
    '--input_file',
    type=click.Path(dir_okay=False, file_okay=True, path_type=pathlib.Path),
    required=True,
    help="Path to the benchmark input file",
)
@click.option(
    '--output_file',
    type=click.Path(dir_okay=False, file_okay=True, path_type=pathlib.Path),
    default="md_benchmark.out",
    help="Path to the benchmark output file",
)
def run_benchmark(input_file, output_file):
    """
    Run a benchmark.
    """
    data_path = input_file.resolve().parent

    with open(input_file, 'r') as f:
        benchmark = json.loads(f.read())

    benchmark_results = {}

    for system in benchmark:
        pdb = data_path / benchmark[system]['protein']
        edge = data_path / benchmark[system]['edge']
        if 'cofactors' in benchmark[system]:
            cofactors = data_path / benchmark[system]['cofactors']
        else:
            cofactors = None
        retval = run_inputs(pdb=pdb, cofactors=cofactors, edge=edge)
        benchmark_results[system] = int(retval)

    with open(output_file, 'w') as f:
        json.dump(benchmark_results, f, indent=4)


if __name__ == "__main__":
    run_benchmark()
