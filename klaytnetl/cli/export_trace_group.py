# MIT License
#
# Modifications Copyright (c) klaytn authors
# Copyright (c) 2018 Evgeny Medvedev, evge.medvedev@gmail.com
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import click
import tempfile
import shutil

from web3 import Web3
from web3.middleware import geth_poa_middleware

from klaytnetl.jobs.export_trace_group_job import ExportTraceGroupJob
from klaytnetl.jobs.exporters.raw_trace_group_item_exporter import (
    raw_trace_group_item_exporter,
)
from klaytnetl.jobs.exporters.enrich_trace_group_item_exporter import (
    enrich_trace_group_item_exporter,
)
from blockchainetl.logging_utils import logging_basic_config
from klaytnetl.providers.auto import get_provider_from_uri
from klaytnetl.thread_local_proxy import ThreadLocalProxy
from klaytnetl.utils import return_provider
from klaytnetl.cli.s3_sync import get_path, sync_to_s3
from klaytnetl.cli.gcs_sync import sync_to_gcs

logging_basic_config()


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option(
    "-s", "--start-block", default=0, show_default=True, type=int, help="Start block"
)
@click.option("-e", "--end-block", required=True, type=int, help="End block")
@click.option(
    "-b",
    "--batch-size",
    default=100,
    show_default=True,
    type=int,
    help="The number of blocks to process at a time.",
)
@click.option(
    "--traces-output",
    default=None,
    type=str,
    help='The output file for traces. If not provided traces will not be exported. Use "-" for stdout',
)
@click.option(
    "--contracts-output",
    default=None,
    type=str,
    help='The output file for contracts. If not provided contracts will not be exported. Use "-" for stdout',
)
@click.option(
    "--tokens-output",
    default=None,
    type=str,
    help='The output file for tokens. If not provided tokens will not be exported. Use "-" for stdout',
)
@click.option(
    "-w",
    "--max-workers",
    default=5,
    show_default=True,
    type=int,
    help="The maximum number of workers.",
)
@click.option(
    "-p",
    "--provider-uri",
    default="https://cypress.fandom.finance/archive",
    show_default=True,
    type=str,
    help="The URI of the web3 provider e.g. "
    "file://$HOME/var/kend/data/klay.ipc or https://cypress.fandom.finance/archive",
)
@click.option(
    "-t",
    "--timeout",
    default=60,
    show_default=True,
    type=int,
    help="The connection time out",
)
@click.option(
    "--enrich",
    default=False,
    show_default=True,
    type=bool,
    help="Enrich output files of trace group",
)
@click.option(
    "--s3-bucket", default=None, type=str, help="S3 bucket for syncing export data."
)
@click.option(
    "--gcs-bucket", default=None, type=str, help="GCS bucket for syncing export data."
)
@click.option(
    "--file-format",
    default="json",
    show_default=True,
    type=str,
    help='Export file format. "json" (default) or "csv".',
)
@click.option(
    "--file-maxlines",
    default=None,
    type=int,
    help="Limit max lines per single file. If not provided, output will be a single file.",
)
@click.option(
    "--compress",
    is_flag=True,
    type=bool,
    help="Enable compress option using gzip. If not provided, the option will be disabled.",
)
@click.option(
    "--network",
    default=None,
    type=str,
    help="Input either baobab or cypress to obtain public provider"
    "If not provided, the option will be disabled.",
)
@click.option(
    "--log-percentage-step",
    default=10,
    type=int,
    help="How often to show log percentage step"
)
def export_trace_group(
    start_block,
    end_block,
    batch_size,
    traces_output,
    contracts_output,
    tokens_output,
    max_workers,
    provider_uri,
    timeout,
    enrich,
    s3_bucket,
    gcs_bucket,
    file_format,
    file_maxlines,
    compress,
    network,
    log_percentage_step,
):
    """Exports traces group from Klaytn node."""
    if network:
        provider_uri = return_provider(network)

    web3 = Web3(get_provider_from_uri(provider_uri))
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    if traces_output is None and contracts_output is None and tokens_output is None:
        raise ValueError(
            "At least one of --traces-output, --contracts-output, or --tokens-output options must be provided"
        )

    if s3_bucket and gcs_bucket:
        raise ValueError(
            "Only one export option is allowed - S3 or GCS"
        )

    if file_format not in {"json", "csv"}:
        raise ValueError('"--file-format" option only supports "json" or "csv".')

    if isinstance(file_maxlines, int) and file_maxlines <= 0:
        file_maxlines = None

    exporter_options = {
        "file_format": file_format,
        "file_maxlines": file_maxlines,
        "compress": compress,
    }

    # s3 export
    if s3_bucket or gcs_bucket:
        tmpdir = tempfile.mkdtemp()
    else:
        tmpdir = None

    # enrich option
    if enrich:
        exporter = enrich_trace_group_item_exporter(
            get_path(tmpdir, traces_output),
            get_path(tmpdir, contracts_output),
            get_path(tmpdir, tokens_output),
            **exporter_options
        )
    else:
        exporter = raw_trace_group_item_exporter(
            get_path(tmpdir, traces_output),
            get_path(tmpdir, contracts_output),
            get_path(tmpdir, tokens_output),
            **exporter_options
        )

    job = ExportTraceGroupJob(
        start_block=start_block,
        end_block=end_block,
        batch_size=batch_size,
        batch_web3_provider=ThreadLocalProxy(
            lambda: get_provider_from_uri(provider_uri, timeout=timeout, batch=True)
        ),
        web3=ThreadLocalProxy(lambda: web3),
        max_workers=max_workers,
        enrich=enrich,
        item_exporter=exporter,
        log_percentage_step=log_percentage_step,
        export_traces=traces_output is not None,
        export_contracts=contracts_output is not None,
        export_tokens=tokens_output is not None,
    )

    job.run()

    if s3_bucket:
        sync_to_s3(
            s3_bucket,
            tmpdir,
            {traces_output, contracts_output, tokens_output},
            file_maxlines is None,
        )
        shutil.rmtree(tmpdir)

    if gcs_bucket:
        sync_to_gcs(
            gcs_bucket,
            tmpdir,
            {traces_output, contracts_output, tokens_output},
            file_maxlines is None,
        )
        shutil.rmtree(tmpdir)
