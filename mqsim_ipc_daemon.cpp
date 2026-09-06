// SPDX-License-Identifier: GPL-2.0-only

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <sched.h>
#include <string>
#include <time.h>
#include <vector>

#include "../nvmevirt/mqsim_ipc_protocol.h"
#include "mqsim_native_online_model.h"
#include "src/sim/Engine.h"

static uint64_t read_latency_ns = 75000;
static uint64_t write_latency_ns = 750000;
static uint64_t controller_overhead_ns = 20000;
static uint64_t transfer_per_4k_ns = 2000;
static const char *ssd_config_path = "ssdconfig.xml";
static const char *preload_extents_path = NULL;
enum class TimingModel { FIXED, NATIVE_ONLINE };
static TimingModel timing_model = TimingModel::FIXED;
static uint64_t trace_first_requests = 0;
static uint64_t stats_every_reqs = 0;
static uint64_t stats_top_n = 8;
static uint64_t completion_window_ns = 0;
static uint64_t request_coalesce_us = 10;
static uint64_t request_coalesce_max = 32;
static bool event_stats = false;
static uint64_t event_trace_first_events = 0;
static bool stats_full = false;

struct DaemonTimingStats {
	uint64_t pop_delay_count = 0;
	uint64_t pop_delay_total_ns = 0;
	uint64_t pop_delay_max_ns = 0;
	uint64_t submit_count = 0;
	uint64_t submit_total_ns = 0;
	uint64_t submit_max_ns = 0;
	uint64_t run_completion_count = 0;
	uint64_t run_completion_total_ns = 0;
	uint64_t run_completion_max_ns = 0;
	uint64_t run_pending_before_total = 0;
	uint64_t run_pending_before_max = 0;
	uint64_t run_pending_after_total = 0;
	uint64_t run_pending_after_max = 0;
	uint64_t run_replies_total = 0;
	uint64_t run_replies_max = 0;
	uint64_t run_sim_advanced_total_ns = 0;
	uint64_t run_sim_advanced_max_ns = 0;
	uint64_t coalesce_round_count = 0;
	uint64_t coalesce_wait_total_ns = 0;
	uint64_t coalesce_wait_max_ns = 0;
	uint64_t coalesce_requests_total = 0;
	uint64_t coalesce_requests_max = 0;
	uint64_t push_response_count = 0;
	uint64_t push_response_total_ns = 0;
	uint64_t push_response_max_ns = 0;
};

static DaemonTimingStats daemon_timing_stats;

static uint64_t monotonic_now_ns()
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void update_max_u64(uint64_t& current, uint64_t value)
{
	if (value > current)
		current = value;
}

static uint64_t avg_or_zero(uint64_t total, uint64_t count)
{
	return count == 0 ? 0 : total / count;
}

static void print_daemon_timing_stats(FILE *out)
{
	fprintf(out, "MQSim daemon bridge timing table:\n");
	fprintf(out, "%28s %12s %14s %14s\n", "Stage", "Count", "Avg(ns)", "Max(ns)");
	fprintf(out, "====================================================================\n");
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"KernelSubmit2DaemonPop",
		daemon_timing_stats.pop_delay_count,
		avg_or_zero(daemon_timing_stats.pop_delay_total_ns, daemon_timing_stats.pop_delay_count),
		daemon_timing_stats.pop_delay_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"DaemonSubmitRequest",
		daemon_timing_stats.submit_count,
		avg_or_zero(daemon_timing_stats.submit_total_ns, daemon_timing_stats.submit_count),
		daemon_timing_stats.submit_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RunUntilCompletion",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_completion_total_ns, daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_completion_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RequestCoalesceWait",
		daemon_timing_stats.coalesce_round_count,
		avg_or_zero(daemon_timing_stats.coalesce_wait_total_ns,
			    daemon_timing_stats.coalesce_round_count),
		daemon_timing_stats.coalesce_wait_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RequestsPerCoalesce",
		daemon_timing_stats.coalesce_round_count,
		avg_or_zero(daemon_timing_stats.coalesce_requests_total,
			    daemon_timing_stats.coalesce_round_count),
		daemon_timing_stats.coalesce_requests_max);
	fprintf(out, "\nMQSim completion batch table:\n");
	fprintf(out, "%28s %12s %14s %14s\n", "Metric", "Count", "Avg", "Max");
	fprintf(out, "====================================================================\n");
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"PendingBeforeRun",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_pending_before_total,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_pending_before_max);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"RepliesGenerated",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_replies_total,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_replies_max);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"PendingAfterRun",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_pending_after_total,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_pending_after_max);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"SimAdvanced(ns)",
		daemon_timing_stats.run_completion_count,
		avg_or_zero(daemon_timing_stats.run_sim_advanced_total_ns,
			    daemon_timing_stats.run_completion_count),
		daemon_timing_stats.run_sim_advanced_max_ns);
	fprintf(out, "%28s %12" PRIu64 " %14" PRIu64 " %14" PRIu64 "\n",
		"PushResponse",
		daemon_timing_stats.push_response_count,
		avg_or_zero(daemon_timing_stats.push_response_total_ns, daemon_timing_stats.push_response_count),
		daemon_timing_stats.push_response_max_ns);
}

static uint32_t load_u32(const uint32_t *ptr)
{
	return __atomic_load_n(ptr, __ATOMIC_ACQUIRE);
}

static void store_u32(uint32_t *ptr, uint32_t value)
{
	__atomic_store_n(ptr, value, __ATOMIC_RELEASE);
}

static bool ring_empty(const nvmev_mqsim_ring_header& ring)
{
	return load_u32(&ring.head) == load_u32(&ring.tail);
}

static bool ring_full(const nvmev_mqsim_ring_header& ring)
{
	return load_u32(&ring.tail) - load_u32(&ring.head) >= load_u32(&ring.size);
}

static uint64_t estimate_latency_ns(const nvmev_mqsim_io_msg& req)
{
	uint64_t bytes = (uint64_t)req.nlb * 512;
	uint64_t chunks_4k = (bytes + 4095) / 4096;
	uint64_t transfer_ns = chunks_4k * transfer_per_4k_ns;

	if (req.opcode == NVMEV_MQSIM_IO_WRITE)
		return controller_overhead_ns + transfer_ns + write_latency_ns;

	return controller_overhead_ns + transfer_ns + read_latency_ns;
}

static bool pop_request(nvmev_mqsim_shm *shm, nvmev_mqsim_io_msg *req)
{
	nvmev_mqsim_ring_header& ring = shm->req_ring;
	uint32_t head;

	if (ring_empty(ring))
		return false;

	head = load_u32(&ring.head);
	__atomic_thread_fence(__ATOMIC_ACQUIRE);
	memcpy(req, &shm->req_entries[head & NVMEV_MQSIM_RING_MASK], sizeof(*req));
	store_u32(&ring.head, head + 1);
	return true;
}

static bool push_response(nvmev_mqsim_shm *shm, const nvmev_mqsim_io_msg& resp)
{
	nvmev_mqsim_ring_header& ring = shm->resp_ring;
	uint32_t tail;

	if (ring_full(ring)) {
		store_u32(&ring.dropped, load_u32(&ring.dropped) + 1);
		return false;
	}

	tail = load_u32(&ring.tail);
	memcpy(&shm->resp_entries[tail & NVMEV_MQSIM_RING_MASK], &resp, sizeof(resp));
	__atomic_thread_fence(__ATOMIC_RELEASE);
	store_u32(&ring.tail, tail + 1);
	return true;
}

static bool msg_valid(const nvmev_mqsim_io_msg& msg)
{
	return msg.magic == NVMEV_MQSIM_MAGIC &&
	       msg.version == NVMEV_MQSIM_VERSION &&
	       msg.type == NVMEV_MQSIM_MSG_IO_REQUEST;
}

static void print_usage(const char *prog)
{
	fprintf(stderr,
		"Usage: %s [--model fixed|online|native] [--ssd-config PATH] [--preload-extents PATH] [--trace-first N] [--stats-every-reqs N] [--stats-top N] [--stats-full] [--completion-window-ns N] [--request-coalesce-us N] [--request-coalesce-max N] [--event-stats] [--event-trace-first-events N] [--read-ns N] [--write-ns N] [--ctrl-ns N] [--xfer-4k-ns N]\n",
		prog);
}

static void record_pop_delay(const nvmev_mqsim_io_msg& req)
{
	uint64_t pop_wall_ns = monotonic_now_ns();

	if (req.kernel_submit_wall_ns > 0 && pop_wall_ns >= req.kernel_submit_wall_ns) {
		uint64_t pop_delay_ns = pop_wall_ns - req.kernel_submit_wall_ns;

		daemon_timing_stats.pop_delay_count++;
		daemon_timing_stats.pop_delay_total_ns += pop_delay_ns;
		update_max_u64(daemon_timing_stats.pop_delay_max_ns, pop_delay_ns);
	}
}

static bool handle_request(nvmev_mqsim_shm *shm,
			   MQSimNativeOnlineModel& native_online_model,
			   const nvmev_mqsim_io_msg& req,
			   bool *produced_response)
{
	nvmev_mqsim_io_msg reply;

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		uint64_t submit_begin_ns;
		uint64_t submit_ns;

		record_pop_delay(req);
		submit_begin_ns = monotonic_now_ns();
		native_online_model.submit_request(req);
		submit_ns = monotonic_now_ns() - submit_begin_ns;
		daemon_timing_stats.submit_count++;
		daemon_timing_stats.submit_total_ns += submit_ns;
		update_max_u64(daemon_timing_stats.submit_max_ns, submit_ns);
		return true;
	}

	reply = req;
	reply.type = NVMEV_MQSIM_MSG_IO_REPLY;
	reply.latency_ns = estimate_latency_ns(req);
	reply.status = 0;
	if (!push_response(shm, reply)) {
		fprintf(stderr, "Response ring is full; dropping request_id=%llu\n",
			(unsigned long long)req.request_id);
		return false;
	}
	if (produced_response)
		*produced_response = true;
	return true;
}

static uint64_t drain_requests(nvmev_mqsim_shm *shm,
			       MQSimNativeOnlineModel& native_online_model,
			       bool *produced_response,
			       uint64_t max_requests)
{
	uint64_t handled = 0;

	while (max_requests == 0 || handled < max_requests) {
		nvmev_mqsim_io_msg req;

		if (!pop_request(shm, &req))
			break;
		if (!msg_valid(req))
			continue;
		if (handle_request(shm, native_online_model, req, produced_response))
			handled++;
	}
	return handled;
}

static uint64_t coalesce_requests(nvmev_mqsim_shm *shm,
				  MQSimNativeOnlineModel& native_online_model,
				  bool *produced_response,
				  uint64_t already_handled)
{
	uint64_t total_handled = already_handled;
	uint64_t start_ns;
	uint64_t deadline_ns;

	if (timing_model != TimingModel::NATIVE_ONLINE ||
	    request_coalesce_us == 0 || request_coalesce_max == 0 ||
	    already_handled >= request_coalesce_max)
		return total_handled;
	if (already_handled == 0 && !native_online_model.has_pending_requests())
		return total_handled;

	start_ns = monotonic_now_ns();
	deadline_ns = start_ns + request_coalesce_us * 1000;
	while (total_handled < request_coalesce_max) {
		uint64_t newly_handled =
			drain_requests(shm, native_online_model, produced_response,
				       request_coalesce_max - total_handled);

		total_handled += newly_handled;
		if (total_handled >= request_coalesce_max)
			break;
		if (monotonic_now_ns() >= deadline_ns)
			break;
		if (newly_handled == 0)
			sched_yield();
	}

	{
		uint64_t waited_ns = monotonic_now_ns() - start_ns;

		daemon_timing_stats.coalesce_round_count++;
		daemon_timing_stats.coalesce_wait_total_ns += waited_ns;
		update_max_u64(daemon_timing_stats.coalesce_wait_max_ns, waited_ns);
		daemon_timing_stats.coalesce_requests_total += total_handled;
		update_max_u64(daemon_timing_stats.coalesce_requests_max, total_handled);
	}
	return total_handled;
}

static bool parse_u64_arg(int argc, char **argv, int *idx, uint64_t *value)
{
	if (*idx + 1 >= argc)
		return false;
	*value = strtoull(argv[++(*idx)], NULL, 10);
	return true;
}

static std::vector<std::string> split_csv_line(const std::string& line)
{
	std::vector<std::string> fields;
	size_t start = 0;

	while (start <= line.size()) {
		size_t pos = line.find(',', start);
		if (pos == std::string::npos) {
			fields.push_back(line.substr(start));
			break;
		}
		fields.push_back(line.substr(start, pos - start));
		start = pos + 1;
	}
	return fields;
}

static bool load_preload_extents(const char *path, MQSimNativeOnlineModel& model)
{
	FILE *file = fopen(path, "r");
	char line[4096];
	uint64_t extent_count = 0;
	uint64_t allocated_pages = 0;

	if (!file) {
		perror("open preload extents");
		return false;
	}

	while (fgets(line, sizeof(line), file)) {
		std::string text(line);
		std::vector<std::string> fields;
		uint64_t slba;
		uint64_t nlb;

		while (!text.empty() && (text[text.size() - 1] == '\n' || text[text.size() - 1] == '\r'))
			text.erase(text.size() - 1);
		if (text.empty() || text[0] == '#')
			continue;

		fields = split_csv_line(text);
		if (fields.size() == 2) {
			slba = strtoull(fields[0].c_str(), NULL, 10);
			nlb = strtoull(fields[1].c_str(), NULL, 10);
		} else if (fields.size() >= 4) {
			slba = strtoull(fields[fields.size() - 2].c_str(), NULL, 10);
			nlb = strtoull(fields[fields.size() - 1].c_str(), NULL, 10);
		} else {
			fprintf(stderr, "Skipping malformed preload extent line: %s\n", text.c_str());
			continue;
		}

		if (nlb == 0)
			continue;
		allocated_pages += model.preload_write(slba, (uint32_t)nlb);
		extent_count++;
	}

	fclose(file);
	printf("MQSim native preload extents loaded. extents=%" PRIu64 " allocated_pages=%" PRIu64 " source=%s\n",
	       extent_count, allocated_pages, path);
	return true;
}

int main(int argc, char **argv)
{
	int fd;
	nvmev_mqsim_shm *shm;
	long page_size = sysconf(_SC_PAGESIZE);
	size_t shm_size;
	struct pollfd pfd;
	MQSimNativeOnlineModel native_online_model;
	uint64_t daemon_request_count = 0;
	uint64_t daemon_reply_count = 0;
	uint64_t next_stats_at = 0;

	if (page_size <= 0) {
		perror("sysconf(_SC_PAGESIZE)");
		return 1;
	}
	shm_size = (sizeof(nvmev_mqsim_shm) + page_size - 1) & ~(page_size - 1);

	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--read-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &read_latency_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--write-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &write_latency_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--ctrl-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &controller_overhead_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--xfer-4k-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &transfer_per_4k_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--trace-first")) {
			if (!parse_u64_arg(argc, argv, &i, &trace_first_requests))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--stats-every-reqs")) {
			if (!parse_u64_arg(argc, argv, &i, &stats_every_reqs))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--stats-top")) {
			if (!parse_u64_arg(argc, argv, &i, &stats_top_n))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--completion-window-ns")) {
			if (!parse_u64_arg(argc, argv, &i, &completion_window_ns))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--request-coalesce-us")) {
			if (!parse_u64_arg(argc, argv, &i, &request_coalesce_us))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--request-coalesce-max")) {
			if (!parse_u64_arg(argc, argv, &i, &request_coalesce_max))
				goto invalid_args;
		} else if (!strcmp(argv[i], "--event-stats")) {
			event_stats = true;
		} else if (!strcmp(argv[i], "--event-trace-first-events")) {
			if (!parse_u64_arg(argc, argv, &i, &event_trace_first_events))
				goto invalid_args;
			event_stats = true;
		} else if (!strcmp(argv[i], "--stats-full")) {
			stats_full = true;
		} else if (!strcmp(argv[i], "--ssd-config")) {
			if (i + 1 >= argc)
				goto invalid_args;
			ssd_config_path = argv[++i];
		} else if (!strcmp(argv[i], "--preload-extents")) {
			if (i + 1 >= argc)
				goto invalid_args;
			preload_extents_path = argv[++i];
		} else if (!strcmp(argv[i], "--model")) {
			if (i + 1 >= argc)
				goto invalid_args;
			const char *model = argv[++i];
			if (!strcmp(model, "online") || !strcmp(model, "native")) {
				timing_model = TimingModel::NATIVE_ONLINE;
			} else if (!strcmp(model, "fixed")) {
				timing_model = TimingModel::FIXED;
			} else {
				goto invalid_args;
			}
		} else {
			goto invalid_args;
		}
	}

	if (stats_every_reqs > 0)
		next_stats_at = stats_every_reqs;

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		std::string error;

		if (!native_online_model.initialize(ssd_config_path, &error)) {
			fprintf(stderr, "MQSim native online model init failed: %s\n", error.c_str());
			return 1;
		}

		if (event_stats)
			Simulator->Enable_event_stats(event_trace_first_events);

		if (preload_extents_path && !load_preload_extents(preload_extents_path, native_online_model))
			return 1;
	}

	fd = open("/dev/" NVMEV_MQSIM_DEVICE_NAME, O_RDWR);
	if (fd < 0) {
		perror("open shared-memory IPC device");
		return 1;
	}

	shm = (nvmev_mqsim_shm *)mmap(NULL, shm_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (shm == MAP_FAILED) {
		perror("mmap shared-memory IPC");
		close(fd);
		return 1;
	}

	if (load_u32(&shm->magic) != NVMEV_MQSIM_MAGIC ||
	    load_u32(&shm->version) != NVMEV_MQSIM_VERSION ||
	    load_u32(&shm->ring_size) != NVMEV_MQSIM_RING_SIZE) {
		fprintf(stderr, "Shared-memory IPC ABI mismatch\n");
		munmap(shm, shm_size);
		close(fd);
		return 1;
	}

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		printf("MQSim shared-memory IPC daemon connected. model=native config=%s %s\n",
		       ssd_config_path, native_online_model.describe().c_str());
	} else {
		printf("MQSim shared-memory IPC daemon connected. model=fixed read=%lu ns write=%lu ns ctrl=%lu ns xfer_4k=%lu ns\n",
		       read_latency_ns, write_latency_ns, controller_overhead_ns, transfer_per_4k_ns);
	}

	pfd.fd = fd;
	pfd.events = POLLIN;

	while (true) {
		int ready;
		bool produced_response = false;
		uint64_t handled_this_loop = 0;

		handled_this_loop = drain_requests(shm, native_online_model, &produced_response, 0);
		handled_this_loop = coalesce_requests(shm, native_online_model, &produced_response,
						      handled_this_loop);
		daemon_request_count += handled_this_loop;

		while (timing_model != TimingModel::FIXED && next_stats_at > 0 &&
		       daemon_request_count >= next_stats_at) {
				if (stats_full)
					native_online_model.print_stats(stdout);
				else
					native_online_model.print_compact_stats(stdout, stats_top_n);
				print_daemon_timing_stats(stdout);
				if (event_stats)
					Simulator->Print_event_stats(stdout, stats_top_n);
				next_stats_at += stats_every_reqs;
		}

		if (timing_model == TimingModel::NATIVE_ONLINE) {
			nvmev_mqsim_io_msg reply;
			std::string trace;

			if (native_online_model.has_pending_requests()) {
				uint64_t run_begin_ns = monotonic_now_ns();
				size_t pending_before = native_online_model.pending_count();
				sim_time_type sim_advanced_ns = 0;
				size_t replies_generated =
					native_online_model.run_completion_batch(
						(sim_time_type)completion_window_ns,
						&sim_advanced_ns);
				size_t pending_after = native_online_model.pending_count();
				uint64_t run_ns = monotonic_now_ns() - run_begin_ns;

				daemon_timing_stats.run_completion_count++;
				daemon_timing_stats.run_completion_total_ns += run_ns;
				update_max_u64(daemon_timing_stats.run_completion_max_ns, run_ns);
				daemon_timing_stats.run_pending_before_total += pending_before;
				update_max_u64(daemon_timing_stats.run_pending_before_max, pending_before);
				daemon_timing_stats.run_pending_after_total += pending_after;
				update_max_u64(daemon_timing_stats.run_pending_after_max, pending_after);
				daemon_timing_stats.run_replies_total += replies_generated;
				update_max_u64(daemon_timing_stats.run_replies_max, replies_generated);
				daemon_timing_stats.run_sim_advanced_total_ns += sim_advanced_ns;
				update_max_u64(daemon_timing_stats.run_sim_advanced_max_ns, sim_advanced_ns);

				if (replies_generated == 0) {
					fprintf(stderr, "MQSim native event queue drained with pending requests\n");
					break;
				}
			}

			while (native_online_model.pop_completed_reply(&reply, &trace)) {
				uint64_t push_begin_ns = monotonic_now_ns();
				bool pushed = push_response(shm, reply);
				uint64_t push_ns = monotonic_now_ns() - push_begin_ns;

				daemon_timing_stats.push_response_count++;
				daemon_timing_stats.push_response_total_ns += push_ns;
				update_max_u64(daemon_timing_stats.push_response_max_ns, push_ns);

				if (!pushed) {
					fprintf(stderr, "Response ring is full; dropping request_id=%llu\n",
						(unsigned long long)reply.request_id);
					continue;
				}
				daemon_reply_count++;
				if (trace_first_requests > 0 && daemon_reply_count <= trace_first_requests)
					printf("MQSim online trace: %s\n", trace.c_str());
				produced_response = true;
			}
		}

		if (handled_this_loop > 0)
			fflush(stdout);

		if (produced_response && ioctl(fd, NVMEV_MQSIM_IOCTL_NOTIFY_RESP) < 0) {
			perror("ioctl notify response");
			break;
		}

		if (timing_model == TimingModel::NATIVE_ONLINE && native_online_model.has_pending_requests())
			continue;

		ready = poll(&pfd, 1, -1);
		if (ready < 0) {
			if (errno == EINTR)
				continue;
			perror("poll");
			break;
		}
	}

	munmap(shm, shm_size);
	close(fd);
	return 0;

invalid_args:
	print_usage(argv[0]);
	return 1;
}
