#!/usr/bin/python3

import argparse
import csv
import json
import math
import os
import requests
import sqlite3 as sl
import sys
import time
from rich.console import Console
from rich.progress import track
from requests_html import HTMLSession
from datetime import datetime
from pathlib import Path
from __init__ import __version__
from __init__ import __prog__

# Get the current timestamp
now = datetime.now()
dt_formatted = now.strftime("%d/%m/%Y %H:%M:%S")

# Define header values
USERAGENT = f"ratatoskr-{__version__}"

# Init rich console
console = Console()

# Init HTML Session
htmlsession = HTMLSession()

# Create filename to save messages if provider is down
# Format YYYY-MM-DD
dt_formatted_filename = now.strftime("%Y-%m-%d")
# Get Process ID
pid = os.getpid()
# Construct filename to save message state
filename = f"ratatoskr_{dt_formatted_filename}_{pid}.json"


def verify_environment(environment_variable):
    """Verify if a provided environment variable has a value and return that value if true"""

    value = os.getenv(environment_variable)
    if not value:
        return None
    else:
        return value


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


def get_gitlab_projectid(session, repository):
    """Parse the Project ID from a given GitLab repository"""

    # Expects HTMLSession() object not requests Session() object
    query_url = f"https://gitlab.com/{repository[0]}/{repository[1]}"
    response = session.get(query_url, timeout=5)

    if response.status_code == 200:
        # Isolate the proper CSS Element and extract value attribute
        project_temp = response.html.find("#project_id")
        projectid = project_temp[0].attrs["value"]
        return projectid

    if response.status_code != 200:
        return None


def get_gitlab_latest_release(session, projectid):
    """Get latest release for given GitLab public project ID"""

    query_url = f"https://gitlab.com/api/v4/projects/{projectid}/releases"
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()

        if response_json == [] and response.status_code == 200:
            console.print(
                f"\n[!] INFO - No release found for project ID {projectid}",
                style="bold yellow",
            )
            return None

    if response.status_code == 404:
        console.print(
            f"[!] WARN - Project {projectid} was not found at {query_url} be sure to confirm the URL",
            style="bold red",
        )

    try:
        latest_release = response_json[0]["_links"].get("self")
    except KeyError:
        return None

    if latest_release:
        return latest_release


def get_gitlab_latest_commit(session, projectid):
    """Get latest commit for given GitLab public project ID"""

    query_url = f"https://gitlab.com/api/v4/projects/{projectid}/repository/commits"
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()

        if response.status_code == 404:
            console.print(
                f"[!] WARN - Project {projectid} was not found at {query_url} be sure to confirm the URL",
                style="bold red",
            )

        latest_commit = response_json[0]
        if latest_commit.get("web_url"):
            return latest_commit["web_url"]
    else:
        return None


def get_latest_release(session, repository):
    """Get the latest release for given repo in ('Owner', 'Repo', 'github') format"""

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
    """Update tracker DB with the latest release and commit"""

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
    """Insert newly identified repository to track"""

    # newrepo = [repo[0], repo[1], dt_formatted, repo[2]]

    sql = "insert into repo (owner, repo, last_updated, website) values(?, ?, ?, ?)"

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(sql, newrepo)
    except sl.IntegrityError as e:
        console.print(
            f"[!] ERROR - Unable to insert new repo into tracker DB", style="bold red"
        )
        console.print(f"{e}")
        sys.exit(1)


def confirm_table(connection):
    """Verify if repo table has been created"""

    cursor = connection.cursor()
    with connection:
        cursor.execute("select * FROM sqlite_master WHERE type='table' and name='repo'")
        data = cursor.fetchall()
        if len(data) == 0:
            return None
        else:
            console.print(f"[+] INFO - Table already exists", style="bold green")
            return True


def delete_repo(connection, repo):
    """Delete repository from tracker db"""

    sql = "DELETE FROM repo WHERE owner = ? AND repo = ?"

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(sql, repo)
    except sl.IntegrityError as e:
        console.print(
            f"[!] ERROR - Unable to delete repo from tracker DB", style="bold red"
        )
        console.print(f"{e}")
        sys.exit(1)


def confirm_repo(connection, repository):
    """Verify if the owner and repository name is already setup in the tracker database"""

    cursor = connection.cursor()
    with connection:
        cursor.execute(
            "select * from repo WHERE owner = ? AND repo = ?",
            [repository[0], repository[1]],
        )
        data = cursor.fetchall()
        if len(data) == 0:
            return None
        else:
            return True


def bootstrap_db(connection):
    """Bootstrap sqlite3 db with REPO table"""

    try:
        cursor = connection.cursor()
        with connection:
            cursor.execute(
                "create table repo (owner, repo, latest_release, latest_commit, last_updated, website)"
            )
    except sl.IntegrityError as e:
        console.print(f"[!] ERROR - Unable to create repo table", style="bold red")
        console.print(f"{e}")
        sys.exit(1)


def dump_table(connection):
    """Print the tracker database"""

    cursor = connection.cursor()
    with connection:
        data = cursor.execute("select * from repo")
        for row in data:
            print(row)


def read_repositories(connection):
    """Return all repositories in the tracker database"""

    repositories = []

    cursor = connection.cursor()
    with connection:
        data = cursor.execute("select * from repo")
        for row in data:
            repositories.append(row)
    return repositories


def save_messages(data, filename):
    """Write messages as JSON to disk in the event webhook is unsuccessful"""

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
    """Send web request to webhook URL"""

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
            "username": "rocket.cat",
            "icon_emoji": ":chipmunk:",
            "attachments": [{"text": message, "color": "#764FA5"}],
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

    # Create mutually exclusive group
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-l",
        "--load",
        action="store_true",
        help="load the repositories to watch into the database",
    )
    group.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="check for new repository releases and commits",
    )
    group.add_argument(
        "-v", "--version", action="version", version=f"{__prog__} {__version__}"
    )
    group.add_argument(
        "-e",
        "--examples",
        action="store_true",
        help="display usage examples and exit",
    )
    parser.add_argument(
        "-p",
        "--provider",
        type=str,
        choices=["rocketchat", "discord", "msteams", "slack"],
        help="provider to use; required with --check",
    )

    # Parse our arguments into internal variables
    args = parser.parse_args()

    # Cleaner variable names
    load = args.load
    check = args.check
    provider = args.provider

    if provider is None:
        console.print(
            f"[!] ERROR - Chat provider was not provided by [green]--provider[/green] argument!",
            style="bold red",
        )
        sys.exit(1)

    return {"Load": load, "Check": check, "Provider": provider}


def prepare_database(filename):
    """Prepare the database"""

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
                f"[+] INFO - Tracker database is already prepared", style="bold green"
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
                    f"[!] ERROR - Database has already been initialized!",
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
                f"[!] ERROR - Database has already been initialized!",
                style="bold red",
            )
            console.print(f"{e}")
            return (False, con)


def main():
    """Main function"""

    # High-level function to parse arguments
    arguments = parse_arguments()

    # Verify tokens and webhook
    github_token = verify_environment("GITHUB_TOKEN")
    gitlab_token = verify_environment("GITLAB_TOKEN")

    # Exit if we don't have GitHub API token
    if not github_token:
        console.print(
            f"[!] ERROR - No GitHub Personal Access Token in environment variables",
            style="bold red",
        )
        sys.exit(1)

    # Exit if we don't have GitLab API Token
    if not gitlab_token:
        console.print(
            f"[!] ERROR - No GitLab Personal Access Token in environment variables",
            style="bold red",
        )
        sys.exit(1)

    prefix = arguments["Provider"].upper()
    webhook_url = verify_environment(f"{prefix}_WEBHOOK")

    # Exit if we don't have webhook URL
    if not webhook_url:
        console.print(
            f"[!] ERROR - No webhook URL found in environment variables",
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
        console.print(f"[!] ERROR - Preparing database!", style="bold red")
        sys.exit(1)

    # Use a friendly name for our connection object
    db_connection_handler = db_prep_result[1]

    # Check rate limits
    github_ratelimit_response = get_ratelimit_status(s_github)

    if github_ratelimit_response is None:
        console.print(f"[!] ERROR Unable to confirm rate limits", style="bold red")
        sys.exit(1)

    # If user provided --load argument, read CSV and load into tracker
    if arguments["Load"]:
        # Extract all the URLs from the first column in the CSV
        repositories = get_urls("GitHub_Tools_List.csv")

        console.print(
            f"[+] Loading repositories to monitor into tracker..", style="bold green"
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
                # Get project ID from project page via scraping
                projectid = get_gitlab_projectid(htmlsession, repo)
                # Get latest release URL
                release = get_gitlab_latest_release(s_gitlab, projectid)
                # Get latest commit URL
                commit = get_gitlab_latest_commit(s_gitlab, projectid)

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
    console.print(f" :chipmunk:  ~ Ratatoskr the Norse Squirrel God ~ :chipmunk:")
    main()
