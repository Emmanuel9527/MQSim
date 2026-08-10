// SPDX-License-Identifier: GPL-2.0-only

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include "../nvmevirt/mqsim_ipc_protocol.h"

static uint64_t read_latency_ns = 75000;
static uint64_t write_latency_ns = 750000;
static uint64_t controller_overhead_ns = 20000;
static uint64_t transfer_per_4k_ns = 2000;

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
		"Usage: %s [--read-ns N] [--write-ns N] [--ctrl-ns N] [--xfer-4k-ns N]\n",
		prog);
}

static bool parse_u64_arg(int argc, char **argv, int *idx, uint64_t *value)
{
	if (*idx + 1 >= argc)
		return false;
	*value = strtoull(argv[++(*idx)], NULL, 10);
	return true;
}

int main(int argc, char **argv)
{
	int fd;
	nvmev_mqsim_shm *shm;
	long page_size = sysconf(_SC_PAGESIZE);
	size_t shm_size;
	struct pollfd pfd;

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
		} else {
			goto invalid_args;
		}
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

	printf("MQSim shared-memory IPC daemon connected. read=%lu ns write=%lu ns ctrl=%lu ns xfer_4k=%lu ns\n",
	       read_latency_ns, write_latency_ns, controller_overhead_ns, transfer_per_4k_ns);

	pfd.fd = fd;
	pfd.events = POLLIN;

	while (true) {
		int ready;
		bool produced_response = false;

		while (true) {
			nvmev_mqsim_io_msg req;
			nvmev_mqsim_io_msg reply;

			if (!pop_request(shm, &req))
				break;
			if (!msg_valid(req))
				continue;

			reply = req;
			reply.type = NVMEV_MQSIM_MSG_IO_REPLY;
			reply.latency_ns = estimate_latency_ns(req);
			reply.status = 0;

			if (!push_response(shm, reply)) {
				fprintf(stderr, "Response ring is full; dropping request_id=%llu\n",
					(unsigned long long)req.request_id);
				continue;
			}
			produced_response = true;
		}

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
