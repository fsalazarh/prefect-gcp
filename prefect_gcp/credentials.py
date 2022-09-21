"""Module handling GCP credentials."""

import functools
from pathlib import Path
from typing import Dict, Optional, Union

import google.auth
import google.auth.transport.requests
from google.oauth2.service_account import Credentials
from pydantic import Json, root_validator, validator

try:
    from google.cloud.bigquery import Client as BigQueryClient
except ModuleNotFoundError:
    pass  # will be raised in get_client

try:
    from google.cloud.secretmanager import SecretManagerServiceClient
except ModuleNotFoundError:
    pass

try:
    from google.cloud.storage import Client as StorageClient
except ModuleNotFoundError:
    pass

from prefect.blocks.core import Block
from prefect.utilities.asyncutils import run_sync_in_worker_thread


def _raise_help_msg(key: str):
    """
    Raises a helpful error message.

    Args:
        key: the key to access HELP_URLS
    """

    def outer(func):
        """
        Used for decorator.
        """

        @functools.wraps(func)
        def inner(*args, **kwargs):
            """
            Used for decorator.
            """
            try:
                return func(*args, **kwargs)
            except NameError as exc:
                raise ImportError(
                    f"To use prefect_gcp.{key}, install prefect-gcp with the "
                    f"'{key}' extra: `pip install 'prefect_gcp[{key}]'`"
                ) from exc

        return inner

    return outer


class GcpCredentials(Block):
    """
    Block used to manage authentication with GCP. GCP authentication is
    handled via the `google.oauth2` module or through the CLI.
    Specify either one of service account_file or service_account_info; if both
    are not specified, the client will try to detect the service account info stored
    in the env from the command, `gcloud auth application-default login`. Refer to the
    [Authentication docs](https://cloud.google.com/docs/authentication/production)
    for more info about the possible credential configurations.

    Attributes:
        service_account_file: Path to the service account JSON keyfile.
        service_account_info: The contents of the keyfile as a dict or JSON string.

    Example:
        Load stored GCP credentials:
        ```python
        from prefect_gcp import GcpCredentials
        gcp_credentials_block = GcpCredentials.load("BLOCK_NAME")
        ```
    """

    _logo_url = "https://images.ctfassets.net/gm98wzqotmnx/4CD4wwbiIKPkZDt4U3TEuW/c112fe85653da054b6d5334ef662bec4/gcp.png?h=250"  # noqa
    _block_type_name = "GCP Credentials"

    service_account_file: Optional[Path] = None
    service_account_info: Optional[Union[Dict[str, str], Json]] = None
    project: Optional[str] = None
    infer_project: bool = False

    @root_validator
    def _provide_one_service_account_source(cls, values):
        """
        Ensure that only a service account file or service account info ias provided.
        """
        both_service_account = (
            values.get("service_account_info") is not None
            and values.get("service_account_file") is not None
        )
        if both_service_account:
            raise ValueError(
                "Only one of service_account_info or service_account_file "
                "can be specified at once"
            )
        return values

    @root_validator
    def _cannot_infer_project_with_specified_project(cls, values):
        if values.get("infer_project") and values.get("project"):
            raise ValueError("Unable to infer project with a project already set")
        return values

    @validator("service_account_file")
    def _check_service_account_file(cls, file):
        """Get full path of provided file and make sure that it exists."""
        if not file:
            return file

        service_account_file = Path(file).expanduser()
        if not service_account_file.exists():
            raise ValueError("The provided path to the service account is invalid")
        return service_account_file

    def block_initialization(self):
        if self.infer_project:
            credentials = self.get_credentials_from_service_account()
            self.project = credentials.project_id

    def get_credentials_from_service_account(self) -> Union[Credentials, None]:
        """
        Helper method to serialize credentials by using either
        service_account_file or service_account_info.
        """
        if self.service_account_info:
            credentials = Credentials.from_service_account_info(
                self.service_account_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        elif self.service_account_file:
            credentials = Credentials.from_service_account_file(
                self.service_account_file,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        else:
            credentials, _ = google.auth.default()
        return credentials

    async def get_access_token(self):
        """
        See: https://stackoverflow.com/a/69107745
        Also: https://www.jhanley.com/google-cloud-creating-oauth-access-tokens-for-rest-api-calls/
        """  # noqa
        request = google.auth.transport.requests.Request()
        credentials = self.get_credentials_from_service_account()
        await run_sync_in_worker_thread(credentials.refresh, request)
        return credentials.token

    @_raise_help_msg("cloud_storage")
    def get_cloud_storage_client(
        self, project: Optional[str] = None
    ) -> "StorageClient":
        """
        Gets an authenticated Cloud Storage client.

        Args:
            project: Name of the project to use; overrides the base
                class's project if provided.

        Returns:
            An authenticated Cloud Storage client.

        Examples:
            Gets a GCP Cloud Storage client from a path.
            ```python
            from prefect import flow
            from prefect_gcp.credentials import GcpCredentials
            @flow()
            def example_get_client_flow():
                service_account_file = "~/.secrets/prefect-service-account.json"
                client = GcpCredentials(
                    service_account_file=service_account_file
                ).get_cloud_storage_client()
            example_get_client_flow()
            ```

            Gets a GCP Cloud Storage client from a dictionary.
            ```python
            from prefect import flow
            from prefect_gcp.credentials import GcpCredentials
            @flow()
            def example_get_client_flow():
                service_account_info = {
                    "type": "service_account",
                    "project_id": "project_id",
                    "private_key_id": "private_key_id",
                    "private_key": "private_key",
                    "client_email": "client_email",
                    "client_id": "client_id",
                    "auth_uri": "auth_uri",
                    "token_uri": "token_uri",
                    "auth_provider_x509_cert_url": "auth_provider_x509_cert_url",
                    "client_x509_cert_url": "client_x509_cert_url"
                }
                client = GcpCredentials(
                    service_account_info=service_account_info
                ).get_cloud_storage_client()
            example_get_client_flow()
            ```
        """
        credentials = self.get_credentials_from_service_account()

        # override class project if method project is provided
        project = project or self.project
        storage_client = StorageClient(credentials=credentials, project=project)
        return storage_client

    @_raise_help_msg("bigquery")
    def get_bigquery_client(
        self, project: str = None, location: str = None
    ) -> "BigQueryClient":
        """
        Gets an authenticated BigQuery client.

        Args:
            project: Name of the project to use; overrides the base
                class's project if provided.
            location: Location to use.

        Returns:
            An authenticated BigQuery client.

        Examples:
            Gets a GCP BigQuery client from a path.
            ```python
            from prefect import flow
            from prefect_gcp.credentials import GcpCredentials
            @flow()
            def example_get_client_flow():
                service_account_file = "~/.secrets/prefect-service-account.json"
                client = GcpCredentials(
                    service_account_file=service_account_file
                ).get_bigquery_client()
            example_get_client_flow()
            ```

            Gets a GCP BigQuery client from a dictionary.
            ```python
            from prefect import flow
            from prefect_gcp.credentials import GcpCredentials
            @flow()
            def example_get_client_flow():
                service_account_info = {
                    "type": "service_account",
                    "project_id": "project_id",
                    "private_key_id": "private_key_id",
                    "private_key": "private_key",
                    "client_email": "client_email",
                    "client_id": "client_id",
                    "auth_uri": "auth_uri",
                    "token_uri": "token_uri",
                    "auth_provider_x509_cert_url": "auth_provider_x509_cert_url",
                    "client_x509_cert_url": "client_x509_cert_url"
                }
                client = GcpCredentials(
                    service_account_info=service_account_info
                ).get_bigquery_client()

            example_get_client_flow()
            ```
        """
        credentials = self.get_credentials_from_service_account()

        # override class project if method project is provided
        project = project or self.project
        big_query_client = BigQueryClient(
            credentials=credentials, project=project, location=location
        )
        return big_query_client

    @_raise_help_msg("secret_manager")
    def get_secret_manager_client(self) -> "SecretManagerServiceClient":
        """
        Gets an authenticated Secret Manager Service client.

        Returns:
            An authenticated Secret Manager Service client.

        Examples:
            Gets a GCP Secret Manager client from a path.
            ```python
            from prefect import flow
            from prefect_gcp.credentials import GcpCredentials
            @flow()
            def example_get_client_flow():
                service_account_file = "~/.secrets/prefect-service-account.json"
                client = GcpCredentials(
                    service_account_file=service_account_file
                ).get_secret_manager_client()
            example_get_client_flow()
            ```

            Gets a GCP Cloud Storage client from a dictionary.
            ```python
            from prefect import flow
            from prefect_gcp.credentials import GcpCredentials
            @flow()
            def example_get_client_flow():
                service_account_info = {
                    "type": "service_account",
                    "project_id": "project_id",
                    "private_key_id": "private_key_id",
                    "private_key": "private_key",
                    "client_email": "client_email",
                    "client_id": "client_id",
                    "auth_uri": "auth_uri",
                    "token_uri": "token_uri",
                    "auth_provider_x509_cert_url": "auth_provider_x509_cert_url",
                    "client_x509_cert_url": "client_x509_cert_url"
                })
                client = GcpCredentials(
                    service_account_info=service_account_info
                ).get_secret_manager_client()
            example_get_client_flow()
            ```
        """
        credentials = self.get_credentials_from_service_account()

        # doesn't accept project; must pass in project in tasks
        secret_manager_client = SecretManagerServiceClient(credentials=credentials)
        return secret_manager_client
