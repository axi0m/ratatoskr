# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Exponential Backoff Algorithm
- Ratelimit support for GitLab and Rocket.Chat Webhooks
- Logging
- Test Slack, Discord, and Microsoft Teams Webook Support
- Use environment variables for webhook definition per https://12factor.net/config

## [0.3.1] - 2021-05-26

### Added

- Fix bug in checking section of main() where a newly inserted repo from loading has a False value in commit and release column
- Remove checking as part of loading new repos from reference CSV

## [0.3.0] - 2021-05-07

### Added

- Fix SQL query in `confirm_table()`
- Add rate limit conditional check and sleep for reset period from GitHub API

## [0.2.1] - 2021-05-04

### Added

- Fixed and tested gitlab integration
- `rich` progress bar added to loading and checking routines
- Added newlines to some console printing to account for rich progress bar
- Added conditional check if trying to prep DB and DB already exists with proper schema

## [0.2.0] - 2021-04-29

### Added

- GitLab Integration
- Rich Console Formatting provided by `rich`

## [0.1.0] - 2021-04-23

### Added

- Initial creation
