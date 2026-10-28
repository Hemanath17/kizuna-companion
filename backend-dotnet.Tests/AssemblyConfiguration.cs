using Xunit;

// Program.cs reads config via process-wide environment variables at host
// startup. Different WebApplicationFactory-based fixtures in this project set
// different values for the same variables; running test classes in parallel
// would let one factory's host build while another's values are in effect.
[assembly: CollectionBehavior(DisableTestParallelization = true)]
