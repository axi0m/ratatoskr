#!/usr/bin/python3

import argparse
import csv
import json
import logging
import os
import sqlite3 as sl
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
from __init__ import __prog__, __version__
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import track

# Get the current timestamp
now = datetime.now()
dt_formatted = now.strftime("%d/%m/%Y %H:%M:%S")

# Create filename to save messages if provider is down
# Format YYYY-MM-DD
dt_formatted_filename = now.strftime("%Y-%m-%d")

# Get Process ID
pid = os.getpid()

# Construct filename to save message state
filename = f"ratatoskr_{dt_formatted_filename}_{pid}.json"

# Create a custom logger
logger = logging.getLogger(__name__)

# Create handlers
c_handler = logging.StreamHandler()
f_handler = logging.FileHandler(f"ratatoskr_{dt_formatted_filename}_{pid}.log")
c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.INFO)

# Create formatters and add it to handlers
c_format = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
f_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)

# Add handlers to the logger
logger.addHandler(c_handler)
logger.addHandler(f_handler)

# Define header values
USERAGENT = f"ratatoskr-{__version__}"

# Init rich console
console = Console()

# Load .env file
load_dotenv()


def verify_environment(environment_variable):
    """Verify if a provided environment variable has a value and return that value if true"""

    value = os.getenv(environment_variable)
    if not value:
        logger.error(f"No environment variable defined for {environment_variable}")
        return None
    else:
        logger.info(
            f"Identified environment variable is present {environment_variable}"
        )
        return value


def verify_gitlab_token(session):
    """Check API if access token is valid

    session - Requests session object with headers applied for GitLab token auth
    """

    query_url = "https://gitlab.com/api/v4/personal_access_tokens"
    response = session.get(query_url, timeout=5)
    response_json = response.json()

    # Check if active
    if response.ok:
        logger.info("Verified GitLab Token is active")
        return True

    # If we don't have 2XX status code
    if not response.ok:
        # Check if we have expired GitLab Token
        if response_json["message"] == "401 Unauthorized":
            console.print(
                "[!] Error - Unauthorized, verify GitLab token!",
                style="bold red",
            )
            logger.error("Invalid token for GitLab!")
            return None


def verify_github_token(session):
    """Check API if access token is valid

    session - Requests session object with headers applied for GitHub token auth
    """
    query_url = "https://api.github.com/user"
    response = session.get(query_url, timeout=5)
    response_json = response.json()

    # Check if we have expired GitHub Token
    if response.status_code == 401:
        console.print(
            f"[!] Error - Unauthorized, verify GitHub token is accurate and not expired! {response_json['message']}",
            style="bold red",
        )
        logger.error("Invalid token for GitHub!")
        return None

    # Check if active
    if response.ok:
        logger.info("Verified GitHub Token is active")
        return True


def get_ratelimit_status(session):
    """Get the rate limit status from GitHub API"""

    query_url = "https://api.github.com/rate_limit"
    response = session.get(query_url, timeout=5)

    if response.status_code == 200:
        requests_remaining = int(response.headers["X-RateLimit-Remaining"])
        requests_reset_time = int(response.headers["X-RateLimit-Reset"])
        result = (requests_remaining, requests_reset_time)
        return result
    if response.status_code != 200:
        return None


def get_urls(filename):
    """Read in list of GitHub repositories to monitor releases"""

    # We use a list of tuples instead of a dictionary since the owners (or keys)
    # may be duplicates for different repos by same owner in URL list
    repositories = []

    with open(filename) as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=",")
        line_count = 0
        for row in csv_reader:
            if line_count == 0:
                line_count += 1
                pass
            else:
                components = row[0].split("/")
                owner = components[3]
                repo = components[4]
                if "gitlab" in row[0]:
                    combo = (owner, repo, "gitlab")
                    repositories.append(combo)
                    line_count += 1
                elif "github" in row[0]:
                    combo = (owner, repo, "github")
                    repositories.append(combo)
                    line_count += 1
                else:
                    line_count += 1
        console.print(f"[*] INFO - Processed {line_count} lines.", style="bold green")
        return repositories


def get_gitlab_latest_release(session, repository):
    """Get latest release for given GitLab public project

    session - A requests Session object
    repository - A tuple like ('Owner', 'Repo', 'gitlab')
    """

    # GitLab API will take URL-encoded namespace/project URI component
    project = urllib.parse.quote_plus(f"{repository[0]}/{repository[1]}")
    query_url = f"https://gitlab.com/api/v4/projects/{project}/releases"
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()

        if response_json == [] and response.status_code == 200:
            console.print(
                f"\n[!] INFO - No release found for project {repository[0]}/{repository[1]}",
                style="bold yellow",
            )
            return None

    if response.status_code == 404:
        console.print(
            f"[!] WARN - Project {repository[0]}/{repository[1]} was not found at {query_url} be sure to confirm the URL",
            style="bold red",
        )

    try:
        latest_release = response_json[0]["_links"].get("self")
    except KeyError:
        return None

    if latest_release:
        return latest_release


def get_gitlab_latest_commit(session, repository):
    """Get latest commit for given GitLab public project

    session - A requests Session object
    repository - A tuple like ('Owner', 'Repo', 'gitlab')
    """

    # GitLab API will take URL-encoded namespace/project URI component
    project = urllib.parse.quote_plus(f"{repository[0]}/{repository[1]}")
    query_url = f"https://gitlab.com/api/v4/projects/{project}/repository/commits"
    response = session.get(query_url, timeout=5)

    # Check if JSON
    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()

        # Check if project does not exist
        if response.status_code == 404:
            console.print(
                f"[!] WARN - Project {repository[0]}/{repository[1]} was not found at {query_url} be sure to confirm the URL",
                style="bold red",
            )

        # Check if we have expired GitLab Token
        if response.status_code == 401:
            console.print(
                f"[!] Error - Your GitLab token is expired! {response_json}",
                style="bold red",
            )
            return None

        latest_commit = response_json[0]

        # Return GitLab latest commit URL
        if latest_commit.get("web_url"):
            return latest_commit["web_url"]
    else:
        return None


def get_latest_release(session, repository):
    """Get the latest GitHub release for given repository

    session - A requests Session object
    repository - A tuple like ('Owner', 'Repo', 'github')
    """

    # Sample input ('outflanknl', 'RedELK', 'github')

    query_url = (
        f"https://api.github.com/repos/{repository[0]}/{repository[1]}/releases/latest"
    )
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()
        if response_json.get("html_url"):
            return response_json["html_url"]
    else:
        return None


def get_latest_commit(session, repository):
    """Get the latest commit for a given list of repos in ('Owner', 'Repo') format"""

    query_url = f"https://api.github.com/repos/{repository[0]}/{repository[1]}/commits"
    response = session.get(query_url, timeout=5)

    if not response:
        return None

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()
        latest_commit = response_json[0]
        if latest_commit.get("html_url"):
            return latest_commit["html_url"]
    else:
        return None


def update_tracker(connection, update):
    """Update tracker DB with the latest release and commit

    connection - SQLite DB Connection Object
    update - List of commit, release, datetime, GitHub username, repo name, and whether GitLab or GitHub
    """

    # update_object = [commit, release, dt_formatted, repo[0], repo[1], repo[2]]
    # sample SQL entry = its-a-feature|Mythic|0|https://github.com/its-a-feature/Mythic/commit/75a46ef1c06e58ffaed2b036c3e4adf67b72bbc4|12/05/2021 12:37:32|github

    sql = "UPDATE repo SET latest_commit = ?, latest_release = ?, last_updated = ? WHERE  owner = ? AND repo = ? AND website = ?"

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(sql, update)
    except sl.IntegrityError as e:
        console.print(
            f"[!] ERROR - Unable to update repo {update[4]} in tracker DB",
            style="bold red",
        )
        console.print(f"{e}")
        sys.exit(1)


def insert_repo(connection, newrepo):
    """Insert newly identified repository to track

    connection - SQLite DB Connection Object
    newrepo - List of user, repo URL, last updated timestamp, GitHub or GitLab
    """

    # newrepo = [repo[0], repo[1], dt_formatted, repo[2]]

    sql = "insert into repo (owner, repo, last_updated, website) values(?, ?, ?, ?)"

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(sql, newrepo)
    except sl.IntegrityError as e:
        console.print(
            "[!] ERROR - Unable to insert new repo into tracker DB", style="bold red"
        )
        console.print(f"{e}")
        sys.exit(1)


def confirm_table(connection):
    """Verify if repo table has been created

    connection - SQLite DB Connection Object
    """

    cursor = connection.cursor()
    with connection:
        cursor.execute("select * FROM sqlite_master WHERE type='table' and name='repo'")
        data = cursor.fetchall()
        if len(data) == 0:
            return None
        else:
            console.print("[+] INFO - Table already exists", style="bold green")
            return True


def delete_repo(connection, repo):
    """Delete repository from tracker db

    connection - SQLite DB Connection Object
    repo - List of owner and repo
    """

    sql = "DELETE FROM repo WHERE owner = ? AND repo = ?"

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(sql, repo)
    except sl.IntegrityError as e:
        console.print(
            "[!] ERROR - Unable to delete repo from tracker DB", style="bold red"
        )
        console.print(f"{e}")
        sys.exit(1)


def confirm_repo(connection, repo):
    """Verify if the owner and repository name is already setup in the tracker database

    connection - SQLite DB Connection Object
    repo - List of owner and repo
    """

    cursor = connection.cursor()
    with connection:
        cursor.execute(
            "select * from repo WHERE owner = ? AND repo = ?",
            [repo[0], repo[1]],
        )
        data = cursor.fetchall()
        if len(data) == 0:
            return None
        else:
            return True


def bootstrap_db(connection):
    """Bootstrap sqlite3 db with REPO table

    connection - SQLite DB Connection Object
    """

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(
                "create table repo (owner, repo, latest_release, latest_commit, last_updated, website)"
            )
    except sl.IntegrityError as e:
        console.print("[!] ERROR - Unable to create repo table", style="bold red")
        console.print(f"{e}")
        sys.exit(1)


def dump_table(connection):
    """Print the tracker database

    connection - SQLite DB Connection Object
    """

    cursor = connection.cursor()
    with connection:
        data = cursor.execute("select * from repo")
        for row in data:
            print(row)


def read_repositories(connection):
    """Return all repositories in the tracker database

    connection - SQLite DB Connection Object
    """

    repositories = []

    cursor = connection.cursor()
    with connection:
        data = cursor.execute("select * from repo")
        for row in data:
            repositories.append(row)
    return repositories


def save_messages(data, filename):
    """Write messages as JSON to disk in the event webhook is unsuccessful

    data - JSON object
    filename - Filname to write to disk
    """

    try:
        with open(filename, "rt") as fh:
            existing_data = json.load(fh)
    except FileNotFoundError as notfound:
        console.print(
            f"[!] WARN - Filename [blue]{filename}[/blue] does not exist [i]will create[/i]: {notfound}",
            style="bold yellow",
        )
        existing_data = []
    except IOError as e:
        console.print(
            f"[!] ERROR - Unable to read file [blue]{filename}[/blue]: {e}",
            style="bold yellow",
        )
        existing_data = []

    # Update the dict object with new data passed to function
    existing_data.append(data)

    # Write our merged JSON object to disk in the event we have to resend the messages
    with open(filename, "wt") as fh:
        json.dump(existing_data, fh)

    console.print(
        f"[!] WARN - Wrote messages to file [blue]{filename}[/blue]",
        style="bold yellow",
    )


def send_webhook(message, webhook_url, provider, filename):
    """Send web request to webhook URL

    message - Message text for chat notification
    webhook_url - Webhook URL to send request to
    provider - Webhook provider to format message
    filename - Filename to write to disk in event of failure
    """

    # https://docs.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook
    if provider == "msteams":
        data = {"Text": message}

    # https://api.slack.com/messaging/webhooks
    if provider == "slack":
        data = {"text": message}

    # https://discord.com/developers/docs/resources/webhook
    if provider == "discord":
        data = {"content": message}

    # https://docs.rocket.chat/guides/administration/admin-panel/integrations
    if provider == "rocketchat":
        data = {
            "emoji": ":chipmunk:",
            "attachments": [
                {"title": "ratatoskr notify", "text": message, "color": "#764FA5"}
            ],
        }

    # HTTP POST to our Webhook URL
    r = requests.post(webhook_url, json=data)

    # Verify successful status code via .ok method on response object
    # Most APIs return 200, Discord returns 204
    if not r.ok:
        console.print(
            f"\n[!] ERROR - Webhook was unsuccessful status code is {r.status_code}: {r.text}",
            style="bold red",
        )
        # Save messages to disk in event we don't succesfully POST
        save_messages(message, filename)

        # Return condition and response object
        return (False, r)

    if r.ok:
        return (True, r)


def parse_arguments():
    """Parse command-line arguments"""

    # Define parser
    parser = argparse.ArgumentParser()

    # Define main group
    arg_group_1 = parser.add_argument_group()
    arg_group_1.add_argument(
        "-p",
        "--provider",
        type=str,
        choices=["rocketchat", "discord", "msteams", "slack"],
        help="provider to use; required with --check",
    )
    arg_group_1.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="check for new repository releases and commits",
    )

    # Create mutually exclusive group
    arg_group_2 = arg_group_1.add_mutually_exclusive_group()
    arg_group_2.add_argument(
        "-l",
        "--load",
        action="store_true",
        help="load the repositories to watch into the database",
    )
    arg_group_2.add_argument(
        "-v", "--version", action="version", version=f"{__prog__} {__version__}"
    )
    arg_group_2.add_argument(
        "-e",
        "--examples",
        action="store_true",
        help="display usage examples and exit",
    )

    # Parse our arguments into internal variables
    args = parser.parse_args()

    # Cleaner variable names
    load = args.load
    check = args.check
    provider = args.provider
    examples = args.examples

    # Display examples if user requested
    if examples:
        console.print("Check latest commits and releases and notify Microsoft Teams")
        console.print(
            f"    {__prog__} [cyan]--check[/cyan] [cyan]--provider[/cyan] msteams",
            style="bold blue",
        )
        console.print("Check latest commits and releases and notify Discord")
        console.print(
            f"    {__prog__} [cyan]--check[/cyan] [cyan]--provider[/cyan] discord",
            style="bold blue",
        )
        console.print("Load the latest reference CSV into tracker database")
        console.print(f"    {__prog__} [cyan]--load[/cyan]", style="bold blue")
        sys.exit(0)

    # Check and webhook provider are required to format the payload
    if check and provider is None:
        console.print(
            "[!] ERROR - Chat provider was not provided by [green]--provider[/green] argument!",
            style="bold red",
        )
        sys.exit(1)

    return {"Load": load, "Check": check, "Provider": provider}


def prepare_database(filename):
    """Prepare the database

    filename - SQLite database filename to track repos
    """

    # Check if tracker.db file exists or not
    p = Path(filename)

    if p.exists():
        console.print(f"[+] INFO - [blue]{filename}[/blue] exists", style="bold green")
        # DB Connection
        con = sl.connect(filename, timeout=5)

        # Confirm the table exists, use the connection object
        confirm_result = confirm_table(con)

        # If the table already exists in the tracker.db file
        if confirm_result:
            console.print(
                "[+] INFO - Tracker database is already prepared", style="bold green"
            )
            return (True, con)

        # If the table does not exist, create it!
        if not confirm_result:
            console.print(
                f"[+] INFO - Preparing database tables in [blue]{filename}[/blue] file..",
                style="bold green",
            )

            # We want to try and handle the sqlite OperationalError condition at least
            try:
                bootstrap_db(con)
                return (True, con)
            except sl.OperationalError as e:
                console.print(
                    "[!] ERROR - Database has already been initialized!",
                    style="bold red",
                )
                console.print(f"{e}")
                return (False, con)

    # If the file does not exist, create it and prepare it
    if not p.exists():
        console.print(
            f"[!] WARN - [blue]{filename}[/blue] does not exist, creating..",
            style="bold yellow",
        )
        # DB Connection
        con = sl.connect(filename, timeout=5)

        # We want to try and handle the sqlite OperationalError condition at least
        try:
            bootstrap_db(con)
            return (True, con)
        except sl.OperationalError as e:
            console.print(
                "[!] ERROR - Database has already been initialized!",
                style="bold red",
            )
            console.print(f"{e}")
            return (False, con)


def main():
    """Main function"""

    # Print a pretty header to console
    console.print(" :chipmunk:  ~ Ratatoskr the Norse Squirrel God ~ :chipmunk:")

    # High-level function to parse arguments
    arguments = parse_arguments()

    # Verify tokens and webhook
    github_token = verify_environment("GITHUB_TOKEN")
    gitlab_token = verify_environment("GITLAB_TOKEN")

    # Exit if we don't have GitHub API token
    if not github_token:
        console.print(
            "[!] ERROR - No GitHub Personal Access Token in environment variables",
            style="bold red",
        )
        sys.exit(1)

    # Exit if we don't have GitLab API Token
    if not gitlab_token:
        console.print(
            "[!] ERROR - No GitLab Personal Access Token in environment variables",
            style="bold red",
        )
        sys.exit(1)

    # Parse provider and format for env check
    if arguments["Provider"]:
        prefix = arguments["Provider"].upper()

        # Verify that our provider webhook is in the environment
        webhook_url = verify_environment(f"{prefix}_WEBHOOK")

        # Exit if we don't have webhook URL
        if not webhook_url:
            console.print(
                "[!] ERROR - No webhook URL found in environment variables",
                style="bold red",
            )
            sys.exit(1)

    # Define our headers
    github_custom_headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": USERAGENT,
        "Authorization": "token {}".format(github_token),
    }
    gitlab_custom_headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": USERAGENT,
        "PRIVATE-TOKEN": "{}".format(gitlab_token),
    }

    # Create reusable HTTP session object
    s_github = requests.Session()
    s_gitlab = requests.Session()

    # Update Session headers
    s_github.headers.update(github_custom_headers)
    s_gitlab.headers.update(gitlab_custom_headers)

    # Prepare the tracker.db file before loading data
    db_prep_result = prepare_database("tracker.db")

    # Ensure we got True from previous function call
    if not db_prep_result[0]:
        console.print("[!] ERROR - Preparing database!", style="bold red")
        sys.exit(1)

    # Use a friendly name for our connection object
    db_connection_handler = db_prep_result[1]

    # Check rate limits
    github_ratelimit_response = get_ratelimit_status(s_github)

    # Check GitHub token validity
    result = verify_github_token(s_github)

    # Check GitLab token validity
    result = verify_gitlab_token(s_gitlab)

    if github_ratelimit_response is None:
        console.print("[!] ERROR Unable to confirm rate limits", style="bold red")
        sys.exit(1)

    # If user provided --load argument, read CSV and load into tracker
    if arguments["Load"]:
        # Extract all the URLs from the first column in the CSV
        repositories = get_urls("GitHub_Tools_List.csv")

        console.print(
            "[+] Loading repositories to monitor into tracker..", style="bold green"
        )

        # We enumerate over all repository URLs
        for repo in track(
            sequence=repositories,
            description="Loading...",
            update_period=1.0,
            auto_refresh=False,
        ):
            # Check if already tracking in database
            confirmation = confirm_repo(db_connection_handler, repo)

            # Verify that repository has been inserted into tracker
            if confirmation:
                pass
            # If it has not been inserted, load it in
            elif not confirmation:
                # Prepare our SQL insert
                newrepo = [repo[0], repo[1], dt_formatted, repo[2]]
                # Perform insert by passing our DB handler and new repository
                insert_repo(db_connection_handler, newrepo)
        sys.exit(0)

    # If user provided --check argument, read all repos from DB
    if arguments["Check"]:
        # Read tracker.db and populate all our repositories in memory
        repositories = read_repositories(db_connection_handler)

        for count, repo in enumerate(
            track(
                sequence=repositories,
                description="Checking repositories...",
                update_period=1.0,
            )
        ):
            if repo[5] == "github":
                # Get latest release URL
                release = get_latest_release(s_github, repo)
                # Get latest commit URL
                commit = get_latest_commit(s_github, repo)
            if repo[5] == "gitlab":
                # Get latest release URL
                release = get_gitlab_latest_release(s_gitlab, repo)
                # Get latest commit URL
                commit = get_gitlab_latest_commit(s_gitlab, repo)

            # Check if latest release matches DB
            if repo[2] != release and release is not None:
                console.print(
                    f"\n[+] New release for repository {repo[1]}: {release}",
                    style="bold green",
                )

                # Update the database
                update = [commit, release, dt_formatted, repo[0], repo[1], repo[5]]
                update_tracker(db_connection_handler, update)

                # Send notification to webhook
                message = f"New release for repository {repo[1]}: {release}"
                response = send_webhook(
                    message, webhook_url, arguments["Provider"], filename
                )

                # If response code is 429, backoff
                if response[1].status_code == 429:
                    delay_time = 60
                    console.print(
                        f"[!] WARN - Too many requests, backing off for [blue]{delay_time}[/blue] seconds"
                    )
                    time.sleep(delay_time)
                    response = send_webhook(
                        message, webhook_url, arguments["Provider"], filename
                    )

            if repo[3] != commit and commit is not None:
                console.print(
                    f"\n[+] New commit for repository {repo[1]}: {commit}",
                    style="bold green",
                )

                # Update the database
                update = [commit, release, dt_formatted, repo[0], repo[1], repo[5]]
                update_tracker(db_connection_handler, update)

                # Send notification to webhook
                message = f"New commit for repository {repo[1]}: {commit}"
                response = send_webhook(
                    message, webhook_url, arguments["Provider"], filename
                )

                # If response code is 429, backoff
                if response[1].status_code == 429:
                    delay_time = 60
                    console.print(
                        f"[!] WARN - Too many requests, backing off for [blue]{delay_time}[/blue] seconds"
                    )
                    time.sleep(delay_time)
                    response = send_webhook(
                        message, webhook_url, arguments["Provider"], filename
                    )


if __name__ == "__main__":
    main()
