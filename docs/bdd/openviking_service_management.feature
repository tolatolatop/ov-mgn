Feature: Isolated OpenViking service management
  ov-mgn manages each logical OpenViking knowledge-base service as an isolated
  candidate and online deployment. A new release is first started on a
  temporary candidate port. After an external check passes, the user promotes
  that exact release to the stable service port.

  Background:
    Given the user has a writable ov-mgn configuration directory
    And Docker is available to run OpenViking containers
    And service secrets are stored in an env file outside the lock files

  Scenario: Generate a candidate deployment plan
    Given a server.json file with service "alpha"
    And service "alpha" has stable host "127.0.0.1"
    And service "alpha" has stable port 18080
    And service "alpha" uses the local source directory "./openviking-alpha"
    And the default temporary port range is 30000 to 39999
    When the user runs "ov-mgn plan"
    Then ov-mgn writes "server.json.lock" as a read-only file
    And the lock contains a generated release id for service "alpha"
    And the lock contains a temporary candidate port from the configured range
    And the lock contains the path to the secret env file
    But the lock does not contain secret env file values

  Scenario: Show and validate a user configuration file
    Given a valid "server.json" file with service "alpha"
    When the user runs "ov-mgn config-file show"
    Then ov-mgn prints the full user configuration
    When the user runs "ov-mgn config-file show services.alpha.stable_port"
    Then ov-mgn prints 18080
    When the user runs "ov-mgn config-file validate"
    Then ov-mgn prints "valid"

  Scenario: Modify simple fields and OpenViking maps
    Given a "server.json" file with service "alpha"
    When the user runs "ov-mgn config-file set defaults.port_range '[31000,31999]'"
    And the user runs "ov-mgn config-file set services.alpha.enabled false"
    And the user runs "ov-mgn config-file set services.alpha.openviking.env.TZ Asia/Shanghai"
    And the user runs "ov-mgn config-file set services.alpha.openviking.vars.profile alpha-prod"
    Then the default temporary port range is 31000 to 31999
    And service "alpha" is disabled
    Then service "alpha" has OpenViking env "TZ" set to "Asia/Shanghai"
    And service "alpha" has OpenViking variable "profile" set to "alpha-prod"

  Scenario: Remove optional fields and OpenViking map keys
    Given a "server.json" file with service "alpha"
    And service "alpha" has an image override
    And service "alpha" has OpenViking env "TZ" set to "Asia/Shanghai"
    When the user runs "ov-mgn config-file unset services.alpha.image"
    And the user runs "ov-mgn config-file unset services.alpha.openviking.env.TZ"
    Then service "alpha" has no image override
    And service "alpha" has no OpenViking env "TZ"

  Scenario: Reject invalid user configuration changes
    Given a valid "server.json" file with service "alpha"
    When the user runs "ov-mgn config-file set services.alpha.openviking.env.bad-key value"
    Then ov-mgn exits with a non-zero status
    And ov-mgn prints a validation error
    And the original "server.json" file is unchanged

  Scenario: Start a candidate release without changing the stable service
    Given "server.json.lock" contains service "alpha"
    When the user runs "ov-mgn up alpha"
    Then ov-mgn copies the local source into the release code directory
    And ov-mgn renders an OpenViking config into the release config directory
    And ov-mgn creates a candidate data directory
    And ov-mgn starts container "ov-mgn-alpha-candidate-{release_id}"
    And the candidate container is bound to the temporary candidate port
    And the stable port 18080 is not changed
    And "state.json" records the candidate release id

  Scenario: Promote a checked candidate to the stable port
    Given service "alpha" has a running candidate release
    And the external health check for the candidate has passed
    When the user runs "ov-mgn promote alpha"
    Then ov-mgn stops the candidate container
    And ov-mgn moves candidate data into the release data directory
    And ov-mgn removes only online containers with stable host "127.0.0.1" and stable port 18080
    And ov-mgn starts container "ov-mgn-alpha-online" for the same release id
    And the online container is bound to "127.0.0.1:18080"
    And "release.json.lock" records the promoted release configuration
    And "state.json" records the online release id
    And "state.json" clears the candidate release id

  Scenario: Inspect deployment status
    Given ov-mgn has generated lock, release, and state files
    When the user runs "ov-mgn status"
    Then ov-mgn prints the lock summary
    And ov-mgn prints the release summary
    And ov-mgn prints runtime state
    And ov-mgn prints matching Docker container status

  Scenario: Keep unrelated services and ports untouched during promotion
    Given service "alpha" has a candidate for stable port 18080
    And service "beta" has an online container for stable port 18081
    When the user runs "ov-mgn promote alpha"
    Then ov-mgn may remove old online containers for stable port 18080
    But ov-mgn does not remove service "beta"
    And ov-mgn does not remove containers bound to stable port 18081
