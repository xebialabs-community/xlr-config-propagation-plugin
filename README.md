# (**Under Construction**) XL Release Configuration Propagation Plugin

## Overview

This plugin helps you propagate parts of XL Release configuration to other XL Release instances. This can be used for example to synchronize shared folders, templates or shared configurations in a federated XL Release setup. Or it can be used to migrate specific folders to a new XL Release instance during a cleanup migration.

## Requirements

XL Release 8.0 or later

## Installation

Copy the plugin JAR file to the `XL_RELEASE_SERVER/plugins` directory and restart the XL Release server.

## Tasks

* Propagate configuration

This task pushes a subset of XL Release folders, templates and shared configurations from one XL Release instance to another. You can configure inclusion patterns of what gets copied, and you can use "dry run" option to see what gets copied before running it for real.

## Releasing

This project uses the gradle-git plugin. So you can release a new version if this project using following commands:

- to release a new patch (default): `./gradlew release -Prelease.scope=patch -Prelease.stage=final`
- to release a new minor release candidate: `./gradlew release -Prelease.scope=minor -Prelease.stage=rc`
Note that your Git repository must be clean to run any stage except for default dev.

When releasing a final version the update of this Gradle plugin will be uploaded to XebiaLabs Nexus.