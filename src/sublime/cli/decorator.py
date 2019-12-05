"""CLI subcommand decorators.

Decorators used to add common functionality to subcommands.

"""
import os
import functools

import click
import structlog
from requests.exceptions import RequestException

from sublime.api import Sublime
from sublime.cli.formatter import FORMATTERS
from sublime.exceptions import RequestFailure, RateLimitError
from sublime.util import load_config

LOGGER = structlog.get_logger()


def echo_result(function):
    """Decorator that prints subcommand results correctly formatted.

    :param function: Subcommand that returns a result from the API.
    :type function: callable
    :returns: Wrapped function that prints subcommand results
    :rtype: callable

    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        result = function(*args, **kwargs)
        context = click.get_current_context()
        params = context.params
        output_format = params["output_format"]
        formatter = FORMATTERS[output_format]
        if isinstance(formatter, dict):
            # For the text formatter, there's a separate formatter for each 
            # subcommand
            formatter = formatter[context.command.name]

        if context.command.name == "enrich":
            # default behavior is to always save the MDM
            # even if no output file is specified
            if not params.get("output_file"):
                input_file_relative_name = params.get('input_file').name
                input_file_relative_no_ext, _ = os.path.splitext(input_file_relative_name)
                input_file_name_no_ext = os.path.basename(input_file_relative_no_ext)
                output_file_name = f'{input_file_name_no_ext}'
                if output_format == "json":
                    output_file_name += ".mdm"
                elif output_format == "txt":
                    output_file_name += ".txt"

                params["output_file"] = click.open_file(output_file_name, mode="w")

            # we always want to print the details to the console
            details_formatter = FORMATTERS["txt"]["enrich_details"]
            output = details_formatter(result, params.get("verbose", False)).strip("\n")
            click.echo(
                output, file=click.open_file("-", mode="w")
            )

            # strip the extra info and just save the MDM
            result = result["message_data_model"]

        output = formatter(result, params.get("verbose", False)).strip("\n")
        click.echo(
            output, file=params.get("output_file", click.open_file("-", mode="w"))
        )

        file_name = params.get("output_file")
        if file_name:
            click.echo(f"Output saved to {file_name.name}")

    return wrapper


def handle_exceptions(function):
    """Print error and exit on API client exception.

    :param function: Subcommand that returns a result from the API.
    :type function: callable
    :returns: Wrapped function that prints subcommand results
    :rtype: callable

    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except RateLimitError as exception:
            body = exception.args[0]
            error_message = "API error: {}".format(body["detail"])
            LOGGER.error(error_message)
            # click.echo(error_message)
            click.get_current_context().exit(-1)
        except RequestFailure as exception:
            body = exception.args[1]
            error_message = "API error: {}".format(body["detail"])
            LOGGER.error(error_message)
            # click.echo(error_message)
            click.get_current_context().exit(-1)
        except RequestException as exception:
            error_message = "API error: {}".format(exception)
            LOGGER.error(error_message)
            # click.echo(error_message)
            click.get_current_context().exit(-1)

    return wrapper


def pass_api_client(function):
    """Create API client form API key and pass it to subcommand.

    :param function: Subcommand that returns a result from the API.
    :type function: callable
    :returns: Wrapped function that prints subcommand results
    :rtype: callable

    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        context = click.get_current_context()
        api_key = context.params.get("api_key")
        config = load_config()

        if api_key is None:
            if not config["api_key"]:
                prog_name = context.parent.info_name
                click.echo(
                    "\nError: API key not found.\n\n"
                    "To fix this problem, please use any of the following methods "
                    "(in order of precedence):\n"
                    "- Pass it using the -k/--api-key option.\n"
                    "- Set it in the SUBLIME_API_KEY environment variable.\n"
                    "- Run {!r} to save it to the configuration file.\n".format(
                        "{} setup".format(prog_name)
                    )
                )
                context.exit(-1)
            api_key = config["api_key"]

        api_client = Sublime(api_key=api_key)
        return function(api_client, *args, **kwargs)

    return wrapper


def enrich_command(function):
    """Decorator that groups decorators common to enrich subcommand."""

    @click.command()
    @click.option("-k", "--api-key", help="Key to include in API requests")
    @click.option(
        "-i", "--input", "input_file", type=click.File(), 
        help="Input EML file", required=True
    )
    @click.option(
        "-o", "--output", "output_file", type=click.File(mode="w"), 
        help=(
            "Output file. Defaults to the input_file name in the current "
            "directory with a .mdm extension if none is specified"
        )
    )
    @click.option(
        "-f",
        "--format",
        "output_format",
        type=click.Choice(["json", "txt"]),
        default="json",
        show_default=True,
        help="Output format",
    )
    @pass_api_client
    @click.pass_context
    @echo_result
    @handle_exceptions
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapper


def analyze_command(function):
    """Decorator that groups decorators common to analyze subcommand."""

    @click.command()
    @click.option("-k", "--api-key", help="Key to include in API requests")
    @click.option(
        "-i", "--input", "input_file", type=click.File(), 
        help="Input EML or enriched MDM file", required=True
    )
    @click.option(
        "-D", "--detections", "detections_file", type=click.File(), 
        help="Detections file [default: detections.pql]"
    )
    @click.option(
        "-d", "--detection", "detection_str", type=str,
        help=(
            "Raw detection. Instead of using a detections file, "
            "specify a single detection to be run directly surrounded "
            "by single quotes"
        )
    )
    @click.option(
        "-o", "--output", "output_file", type=click.File(mode="w"), 
        help="Output file"
    )
    @click.option(
        "-f",
        "--format",
        "output_format",
        type=click.Choice(["json", "txt"]),
        default="txt",
        help="Output format",
    )
    @pass_api_client
    @click.pass_context
    @echo_result
    @handle_exceptions
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        if not kwargs.get('detections_file') and not kwargs.get('detection_str'):
            try:
                detections_file = click.open_file("detections.pql", mode="r")
                kwargs['detections_file'] = detections_file
            except FileNotFoundError as e:
                raise MissingDetectionInput

        return function(*args, **kwargs)

    return wrapper


def query_command(function):
    """Decorator that groups decorators common to query subcommand."""

    @click.command()
    @click.option("-k", "--api-key", help="Key to include in API requests")
    @click.option(
        "-i", "--input", "input_file", type=click.File(), 
        help="Enriched MDM file", required=True
    )
    @click.option(
        "-q", "--query", "query", type=str, required=True,
        help=(
            "Raw query, surrounded by single quotes"
        )
    )
    @click.option(
        "-o", "--output", "output_file", type=click.File(mode="w"), 
        help="Output file"
    )
    @click.option(
        "-f",
        "--format",
        "output_format",
        type=click.Choice(["json", "txt"]),
        default="txt",
        help="Output format",
    )
    @pass_api_client
    @click.pass_context
    @echo_result
    @handle_exceptions
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapper


class MissingDetectionInput(click.ClickException):
    """Exception used for analyze commands missing a detection file or raw query
    """

    def __init__(self):
        message = (
                "You must specify either a .pql detections file (-D) or a raw "
                "detection (-d)")
        super(MissingDetectionInput, self).__init__(message)


class SubcommandNotImplemented(click.ClickException):
    """Exception used temporarily for subcommands that have not been implemented.

    :param subcommand_name: Name of the subcommand to display in the error message.
    :type subcommand_function: str

    """

    def __init__(self, subcommand_name):
        message = "{!r} subcommand is not implemented yet.".format(subcommand_name)
        super(SubcommandNotImplemented, self).__init__(message)


def not_implemented_command(function):
    """Decorator that sends requests for not implemented commands."""

    @click.command()
    @pass_api_client
    @functools.wraps(function)
    def wrapper(api_client, *args, **kwargs):
        command_name = function.__name__
        try:
            api_client.not_implemented(command_name)
        except RequestFailure:
            raise SubcommandNotImplemented(command_name)

    return wrapper
