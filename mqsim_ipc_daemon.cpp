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

#include <string>
#include <vector>

#include "../nvmevirt/mqsim_ipc_protocol.h"
#include "mqsim_native_online_model.h"

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
static bool stats_full = false;

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
		"Usage: %s [--model fixed|online|native] [--ssd-config PATH] [--preload-extents PATH] [--trace-first N] [--stats-every-reqs N] [--stats-top N] [--stats-full] [--read-ns N] [--write-ns N] [--ctrl-ns N] [--xfer-4k-ns N]\n",
		prog);
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

	if (timing_model == TimingModel::NATIVE_ONLINE) {
		std::string error;

		if (!native_online_model.initialize(ssd_config_path, &error)) {
			fprintf(stderr, "MQSim native online model init failed: %s\n", error.c_str());
			return 1;
		}

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

		while (true) {
			nvmev_mqsim_io_msg req;
			nvmev_mqsim_io_msg reply;

			if (!pop_request(shm, &req))
				break;
			if (!msg_valid(req))
				continue;

			reply = req;
			reply.type = NVMEV_MQSIM_MSG_IO_REPLY;
			if (timing_model == TimingModel::NATIVE_ONLINE)
				reply.latency_ns = native_online_model.estimate_latency_ns(req);
			else
				reply.latency_ns = estimate_latency_ns(req);
			reply.status = 0;
			daemon_request_count++;
			handled_this_loop++;

			if (timing_model != TimingModel::FIXED && trace_first_requests > 0 &&
			    daemon_request_count <= trace_first_requests) {
				printf("MQSim online trace: %s\n", native_online_model.last_trace().c_str());
			}

			if (timing_model != TimingModel::FIXED && stats_every_reqs > 0 &&
			    daemon_request_count % stats_every_reqs == 0) {
				if (stats_full)
					native_online_model.print_stats(stdout);
				else
					native_online_model.print_compact_stats(stdout, stats_top_n);
			}

			if (!push_response(shm, reply)) {
				fprintf(stderr, "Response ring is full; dropping request_id=%llu\n",
					(unsigned long long)req.request_id);
				continue;
			}
			produced_response = true;
		}

		if (handled_this_loop > 0)
			fflush(stdout);

		if (produced_response && ioctl(fd, NVMEV_MQSIM_IOCTL_NOTIFY_RESP) < 0) {
			perror("ioctl notify response");
			break;
		}

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
