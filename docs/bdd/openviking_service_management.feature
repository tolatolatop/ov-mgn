Feature: Gateway OpenViking service management
  ov-mgn manages each logical OpenViking service as an isolated backend release
  behind a single Nginx gateway. A new release is first exposed through the
  candidate preview path. After an external check passes, the user promotes
  that exact backend release to the stable route.

  Background:
    Given the user has a writable ov-mgn configuration directory
    And Docker is available to run OpenViking containers
    And service secrets are stored outside the lock files
    And gateway mode is enabled

  Scenario: Generate a candidate deployment plan
    Given a server.json file with service "alpha"
    And the gateway listens on "127.0.0.1:18080"
    And service "alpha" has route path "/alpha/"
    And service "alpha" uses the local source directory "./openviking-alpha"
    When the user runs "ov-mgn plan"
    Then ov-mgn writes "server.json.lock" as a read-only file
    And the lock contains a generated release id for service "alpha"
    And the lock contains backend container "ov-mgn-alpha-{release_id}"
    And the lock contains the path to the secret env file
    But the lock does not contain secret env file values

  Scenario: Show and validate a user configuration file
    Given a valid "server.json" file with service "alpha"
    When the user runs "ov-mgn config-file show"
    Then ov-mgn prints the full user configuration
    When the user runs "ov-mgn config-file show defaults.gateway.port"
    Then ov-mgn prints 18080
    When the user runs "ov-mgn config-file validate"
    Then ov-mgn prints "valid"

  Scenario: Modify simple fields and OpenViking maps
    Given a "server.json" file with service "alpha"
    When the user runs "ov-mgn config-file set defaults.gateway.port 18081"
    And the user runs "ov-mgn config-file set services.alpha.route_path /alpha/"
    And the user runs "ov-mgn config-file set services.alpha.enabled false"
    And the user runs "ov-mgn config-file set services.alpha.openviking.env.TZ Asia/Shanghai"
    And the user runs "ov-mgn config-file set services.alpha.openviking.vars.profile alpha-prod"
    Then the gateway port is 18081
    And service "alpha" route path is "/alpha/"
    And service "alpha" is disabled
    And service "alpha" has OpenViking env "TZ" set to "Asia/Shanghai"
    And service "alpha" has OpenViking variable "profile" set to "alpha-prod"

  Scenario: Reject invalid user configuration changes
    Given a valid "server.json" file with service "alpha"
    When the user runs "ov-mgn config-file set defaults.gateway.enabled false"
    Then ov-mgn exits with a non-zero status
    And ov-mgn prints a validation error
    And the original "server.json" file is unchanged

  Scenario: Start a candidate backend release
    Given "server.json.lock" contains service "alpha"
    When the user runs "ov-mgn up alpha"
    Then ov-mgn copies the local source into the release code directory
    And ov-mgn renders an OpenViking config into the release config directory
    And ov-mgn creates the release data directory
    And ov-mgn starts backend container "ov-mgn-alpha-{release_id}"
    And the backend container is not bound to a host port
    And Nginx serves a service directory at "/__ov-mgn/"
    And Nginx serves service directory JSON at "/__ov-mgn/services.json"
    And Nginx routes "/alpha/__candidate/" to that backend container
    And "state.json.lock" records the candidate release id

  Scenario: Promote a checked candidate to the stable route
    Given service "alpha" has a running candidate backend release
    And the external health check for "/alpha/__candidate/" has passed
    When the user runs "ov-mgn promote alpha"
    Then ov-mgn does not restart the backend container
    And ov-mgn writes "release.json.lock"
    And Nginx routes "/alpha/" to that backend container
    And Nginx returns 404 for "/alpha/__candidate/"
    And "state.json.lock" records the online release id
    And "state.json.lock" clears the candidate release id

  Scenario: Switch the stable route to a running old release
    Given service "alpha" has a running backend release "alpha-old"
    When the user runs "ov-mgn switch alpha alpha-old"
    Then ov-mgn verifies backend container "ov-mgn-alpha-alpha-old" is running
    And Nginx routes "/alpha/" to "ov-mgn-alpha-alpha-old"
    And "state.json.lock" records "alpha-old" as the online release id

  Scenario: Inspect deployment status
    Given ov-mgn has generated lock, release, and state files
    When the user runs "ov-mgn status"
    Then ov-mgn prints the lock summary
    And ov-mgn prints the release summary
    And ov-mgn prints runtime state
    And ov-mgn checks the backend container
    And ov-mgn checks the gateway container
    And ov-mgn checks that Nginx routes match the runtime state
