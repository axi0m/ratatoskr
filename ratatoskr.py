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
from __init__ import __version__
from __init__ import __prog__

# TODO: Add handling for HTTP 429 from GitLab and "Retry-After"
# TODO: Add handling for HTTP 403 from GitHub and "X-RateLimit-Reset"
# TODO: Add handling for webhook rate limiting for Rocket.Chat
# TODO: Add function call during load to delete records
# TODO: Add decent logging
# TODO: Add --verbose parameter after logging enabled

# Get the current timestamp
now = datetime.now()
dt_formatted = now.strftime("%d/%m/%Y %H:%M:%S")

# Define header values
USERAGENT = f"ratatoskr-{__version__}"

# Rocket Webhook URL
webhook_url = "REDACTED"

# Init rich console
console = Console()

# Init HTML Session
htmlsession = HTMLSession()

# DB Connection
con = sl.connect("tracker.db", timeout=5)


def verify_token(environment_variable):
    """Ensure we have our personal access token"""

    token = os.getenv(environment_variable)
    if not token:
        return False
    else:
        return token


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
        return False


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
        # Isolate the proper CSS Element Text
        project_temp = response.html.find(".btn-tertiary")[0].text
        split_components = project_temp.split(" ")
        projectid = split_components[2]
        return projectid

    if response.status_code != 200:
        return False


def get_gitlab_latest_release(session, projectid):
    """Get latest release for given GitLab public project ID"""

    query_url = f"https://gitlab.com/api/v4/projects/{projectid}/releases"
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()
    else:
        console.print(f"[!] Response content is not in JSON format", style="bold red")
        return False

    if response_json == [] and response.status_code == 200:
        console.print(
            f"\n[!] INFO - No release found for project ID {projectid}",
            style="bold yellow",
        )
        return False

    if response.status_code == 404:
        console.print(
            f"[!] WARN - Project {projectid} was not found at {query_url} be sure to confirm the URL",
            style="bold red",
        )

    try:
        latest_release = response_json[0]["_links"].get("self")
    except KeyError:
        return False

    if latest_release:
        return latest_release


def get_gitlab_latest_commit(session, projectid):
    """Get latest commit for given GitLab public project ID"""

    query_url = f"https://gitlab.com/api/v4/projects/{projectid}/repository/commits"
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()
    else:
        console.print(f"[!] Response content is not in JSON format", style="bold red")
        return False

    if response.status_code == 404:
        console.print(
            f"[!] WARN - Project {projectid} was not found at {query_url} be sure to confirm the URL",
            style="bold red",
        )

    latest_commit = response_json[0]
    if latest_commit.get("web_url"):
        return latest_commit["web_url"]
    else:
        print(response_json[0])
        return False


def get_latest_release(session, repository):
    """Get the latest release for given repo in ('Owner', 'Repo', 'github') format"""

    # Sample input ('outflanknl', 'RedELK', 'github')

    query_url = (
        f"https://api.github.com/repos/{repository[0]}/{repository[1]}/releases/latest"
    )
    response = session.get(query_url, timeout=5)

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()
    else:
        console.print(f"[!] Response content is not in JSON format", style="bold red")
        return False

    if response_json.get("html_url"):
        return response_json["html_url"]
    else:
        return False


def get_latest_commit(session, repository):
    """Get the latest commit for a given list of repos in ('Owner', 'Repo') format"""

    query_url = f"https://api.github.com/repos/{repository[0]}/{repository[1]}/commits"
    response = session.get(query_url, timeout=5)

    if not response:
        return False

    if "json" in response.headers.get("Content-Type"):
        response_json = response.json()
    else:
        console.print(f"[!] Response content is not in JSON format", style="bold red")
        return False

    latest_commit = response_json[0]
    if latest_commit.get("html_url"):
        return latest_commit["html_url"]
    else:
        return False


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
            return False
        else:
            console.print(f"[!] INFO - Table already exists", style="bold yellow")
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
            return False
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


def save_messages(data):
    """Write messages as JSON to disk in the event Rocket.Chat is unavailable"""

    with open("saved_messages.json", "w") as write_file:
        json.dump(data, write_file)
    console.print(f"[+] INFO - Wrote messages to disk", style="bold green")


def rocket_alert(message, webhook_url):
    """Generate rocketchat webhook alert"""

    data = {
        "username": "rocket.cat",
        "icon_emoji": ":chipmunk:",
        "attachments": [{"text": message, "color": "#764FA5"}],
    }

    # HTTP POST to our Webhook URL
    r = requests.post(webhook_url, json=data)

    # Verify 200
    if r.status_code != 200:
        console.print(
            f"[!] ERROR - POST request to RocketChat was unsuccessful: {r}",
            style="bold red",
        )
        save_messages(data)
    if r.status_code == 200:
        console.print(
            f"[+] INFO - Webhook successfully POSTed to [blue]{webhook_url}[/blue]",
            style="bold green",
        )


def main():
    """Main function"""

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--prep", action="store_true", help="Initialize the sqlite3 database"
    )
    group.add_argument(
        "--load",
        action="store_true",
        help="Load the repositories to watch into the database",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Check for new repository releases and commits",
    )
    group.add_argument(
        "--version", action="version", version=f"{__prog__} {__version__}"
    )

    # Parse our arguments into internal variables
    args = parser.parse_args()

    # Cleaner variable names
    prep = args.prep
    load = args.load
    check = args.check

    # Verify token
    github_token = verify_token("GITHUB_TOKEN")
    gitlab_token = verify_token("GITLAB_TOKEN")

    # Exit if we don't have an API token
    if not github_token:
        console.print(
            f"[!] ERROR No GitHub OAuth Token in environment variables",
            style="bold red",
        )
        sys.exit(1)

    if not gitlab_token:
        console.print(
            f"[!] ERROR No GitLab OAuth Token in environment variables",
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
    if prep:
        confirm_result = confirm_table(con)
        if confirm_result:
            console.print(
                f"[!] Tracker database is already prepared", style="bold yellow"
            )
            sys.exit(1)
        else:
            console.print(
                f"[+] INFO Preparing database tables in tracker.db file..",
                style="bold green",
            )
            try:
                bootstrap_db(con)
                sys.exit(0)
            except sl.OperationalError as e:
                console.print(
                    f"[!] ERROR database has already been initialized!",
                    style="bold red",
                )
                console.print(f"{e}")
                sys.exit(1)

    # Check rate limits
    github_ratelimit_response = get_ratelimit_status(s_github)

    if github_ratelimit_response is False:
        console.print(f"[!] ERROR Unable to confirm rate limits", style="bold red")
        sys.exit(1)

    if load:
        repositories = get_urls("GitHub_Tools_List.csv")
        console.print(
            f"[+] Loading repositories to monitor into tracker..", style="bold green"
        )
        for repo in track(
            sequence=repositories, description="Loading...", update_period=1.0
        ):
            # Check if already tracking in database
            confirmation = confirm_repo(con, repo)
            if confirmation:
                pass
            elif not confirmation:
                console.print(
                    f"[+] INFO repo {repo[1]} is not tracked...adding to tracker",
                    style="bold green",
                )
                newrepo = [repo[0], repo[1], dt_formatted, repo[2]]
                insert_repo(con, newrepo)
        sys.exit(0)

    if check:
        repositories = read_repositories(con)
        if github_ratelimit_response[0] // len(repositories) == 0:
            console.print(
                f"[!] WARN - Predicting GitHub rate limits based on remaining requests",
                style="bold red",
            )
            # Process X-RateLimit-Reset epoch timestamp
            reset_time_epoch = github_ratelimit_response[1]

            # Convert now datetime.datetime object to epoch timestamp with milliseconds, use math.floor to round to nearest second
            current_time_epoch = math.floor(now.timestamp())

            # Find difference and sleep
            difference_in_epoch = reset_time_epoch - current_time_epoch
            time.sleep(difference_in_epoch)

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
            if repo[2] != release and not None:
                console.print(
                    f"\n[+] NEW release for repository {repo[1]}: {release}",
                    style="bold green",
                )

                # Update the database
                update = [commit, release, dt_formatted, repo[0], repo[1], repo[5]]
                update_tracker(con, update)

                # Send notification to Rocket.Chat webhook
                message = f"New release for repository {repo[1]}: {release}"
                rocket_alert(message, webhook_url)

            if repo[3] != commit and not None:
                console.print(
                    f"\n[+] NEW commit for repository {repo[1]}: {commit}",
                    style="bold green",
                )

                # Update the database
                update = [commit, release, dt_formatted, repo[0], repo[1], repo[5]]
                update_tracker(con, update)

                # Send notification to Rocket.Chat webhook
                message = f"New commit for repository {repo[1]}: {commit}"
                rocket_alert(message, webhook_url)


if __name__ == "__main__":
    main()
