#include "mqsim_online_model.h"

#include <algorithm>
#include <fstream>
#include <inttypes.h>
#include <limits>
#include <sstream>

#include "src/utils/rapidxml/rapidxml.hpp"

MQSimOnlineModel::MQSimOnlineModel()
{
	cfg.channel_count = 8;
	cfg.channel_width = 1;
	cfg.channel_transfer_rate_mtps = 333;
	cfg.chips_per_channel = 4;
	cfg.dies_per_chip = 2;
	cfg.planes_per_die = 2;
	cfg.page_capacity_bytes = 8192;
	cfg.read_latency_ns = 75000;
	cfg.write_latency_ns = 750000;
	cfg.controller_overhead_ns = 20000;
	total_requests = 0;
	total_pages = 0;
	preload_pages = 0;
	write_allocated_pages = 0;
	read_mapped_pages = 0;
	read_unmapped_pages = 0;
	next_physical_page = 0;
	reset_resources();
}

static rapidxml::xml_node<> *child_node(rapidxml::xml_node<> *node, const char *name)
{
	return node ? node->first_node(name) : NULL;
}

static bool parse_u32(rapidxml::xml_node<> *parent, const char *name, uint32_t *value)
{
	rapidxml::xml_node<> *node = child_node(parent, name);
	if (!node || !node->value())
		return false;
	*value = (uint32_t)strtoul(node->value(), NULL, 10);
	return true;
}

static bool parse_u64(rapidxml::xml_node<> *parent, const char *name, uint64_t *value)
{
	rapidxml::xml_node<> *node = child_node(parent, name);
	if (!node || !node->value())
		return false;
	*value = strtoull(node->value(), NULL, 10);
	return true;
}

bool MQSimOnlineModel::load_config(const std::string& path, std::string *error)
{
	std::ifstream file(path.c_str(), std::ios::in | std::ios::binary);
	std::stringstream buffer;
	std::string xml_text;

	if (!file) {
		if (error)
			*error = "cannot open SSD config: " + path;
		return false;
	}

	buffer << file.rdbuf();
	xml_text = buffer.str();
	xml_text.push_back('\0');

	try {
		rapidxml::xml_document<> doc;
		doc.parse<0>(&xml_text[0]);

		rapidxml::xml_node<> *root = doc.first_node("Execution_Parameter_Set");
		rapidxml::xml_node<> *device = child_node(root, "Device_Parameter_Set");
		rapidxml::xml_node<> *flash = child_node(device, "Flash_Parameter_Set");

		if (!device || !flash) {
			if (error)
				*error = "SSD config does not contain Device_Parameter_Set/Flash_Parameter_Set";
			return false;
		}

		parse_u32(device, "Flash_Channel_Count", &cfg.channel_count);
		parse_u32(device, "Flash_Channel_Width", &cfg.channel_width);
		parse_u32(device, "Channel_Transfer_Rate", &cfg.channel_transfer_rate_mtps);
		parse_u32(device, "Chip_No_Per_Channel", &cfg.chips_per_channel);
		parse_u32(flash, "Die_No_Per_Chip", &cfg.dies_per_chip);
		parse_u32(flash, "Plane_No_Per_Die", &cfg.planes_per_die);
		parse_u32(flash, "Page_Capacity", &cfg.page_capacity_bytes);
		parse_u64(flash, "Page_Read_Latency_LSB", &cfg.read_latency_ns);
		parse_u64(flash, "Page_Program_Latency_LSB", &cfg.write_latency_ns);

		if (cfg.channel_count == 0 || cfg.channel_width == 0 ||
		    cfg.channel_transfer_rate_mtps == 0 || cfg.chips_per_channel == 0 ||
		    cfg.dies_per_chip == 0 || cfg.planes_per_die == 0 ||
		    cfg.page_capacity_bytes == 0) {
			if (error)
				*error = "SSD config contains zero-valued topology fields";
			return false;
		}
	} catch (const std::exception& ex) {
		if (error)
			*error = std::string("failed to parse SSD config: ") + ex.what();
		return false;
	}

	reset_resources();
	return true;
}

std::string MQSimOnlineModel::describe() const
{
	std::ostringstream out;
	out << "channels=" << cfg.channel_count
	    << " chips/channel=" << cfg.chips_per_channel
	    << " dies/chip=" << cfg.dies_per_chip
	    << " planes/die=" << cfg.planes_per_die
	    << " page=" << cfg.page_capacity_bytes
	    << " read=" << cfg.read_latency_ns
	    << " write=" << cfg.write_latency_ns
	    << " channel=" << cfg.channel_transfer_rate_mtps << "MT/s"
	    << " mapped_pages=" << mapping_table.size();
	return out.str();
}

void MQSimOnlineModel::reset_resources()
{
	uint64_t lun_count = (uint64_t)cfg.channel_count * cfg.chips_per_channel * cfg.dies_per_chip;
	lun_next_available_ns.assign((size_t)lun_count, 0);
	channel_next_available_ns.assign(cfg.channel_count, 0);
	lun_read_ops.assign((size_t)lun_count, 0);
	lun_write_ops.assign((size_t)lun_count, 0);
	channel_read_ops.assign(cfg.channel_count, 0);
	channel_write_ops.assign(cfg.channel_count, 0);
	total_requests = 0;
	total_pages = 0;
	read_mapped_pages = 0;
	read_unmapped_pages = 0;
}

uint64_t MQSimOnlineModel::transfer_latency_ns(uint64_t bytes) const
{
	uint64_t denominator = (uint64_t)cfg.channel_width * cfg.channel_transfer_rate_mtps;

	return (bytes * 1000 + denominator - 1) / denominator;
}

uint64_t MQSimOnlineModel::page_service_latency_ns(uint32_t opcode, uint64_t bytes) const
{
	uint64_t array_latency = opcode == NVMEV_MQSIM_IO_WRITE ? cfg.write_latency_ns : cfg.read_latency_ns;

	return array_latency + transfer_latency_ns(bytes);
}

uint64_t MQSimOnlineModel::map_lpn_to_channel(uint64_t lpn) const
{
	return lpn % cfg.channel_count;
}

uint64_t MQSimOnlineModel::map_lpn_to_chip(uint64_t lpn) const
{
	uint64_t stripe = lpn / cfg.channel_count;

	return stripe % cfg.chips_per_channel;
}

uint64_t MQSimOnlineModel::map_lpn_to_die(uint64_t lpn) const
{
	uint64_t stripe = lpn / cfg.channel_count;

	return (stripe / cfg.chips_per_channel) % cfg.dies_per_chip;
}

uint64_t MQSimOnlineModel::map_lpn_to_plane(uint64_t lpn) const
{
	uint64_t stripe = lpn / cfg.channel_count;

	return (stripe / ((uint64_t)cfg.chips_per_channel * cfg.dies_per_chip)) % cfg.planes_per_die;
}

uint64_t MQSimOnlineModel::map_lpn_to_lun(uint64_t lpn) const
{
	uint64_t channel = map_lpn_to_channel(lpn);
	uint64_t chip = map_lpn_to_chip(lpn);
	uint64_t die = map_lpn_to_die(lpn);

	return ((channel * cfg.chips_per_channel) + chip) * cfg.dies_per_chip + die;
}

MQSimOnlineModel::PPA MQSimOnlineModel::deterministic_ppa(uint64_t lpn) const
{
	PPA ppa;

	ppa.channel = (uint32_t)map_lpn_to_channel(lpn);
	ppa.chip = (uint32_t)map_lpn_to_chip(lpn);
	ppa.die = (uint32_t)map_lpn_to_die(lpn);
	ppa.plane = (uint32_t)map_lpn_to_plane(lpn);
	ppa.lun = (uint32_t)map_lpn_to_lun(lpn);
	return ppa;
}

MQSimOnlineModel::PPA MQSimOnlineModel::allocate_physical_page()
{
	uint64_t page = next_physical_page++;
	PPA ppa;

	ppa.channel = page % cfg.channel_count;
	page /= cfg.channel_count;
	ppa.chip = page % cfg.chips_per_channel;
	page /= cfg.chips_per_channel;
	ppa.die = page % cfg.dies_per_chip;
	page /= cfg.dies_per_chip;
	ppa.plane = page % cfg.planes_per_die;
	ppa.lun = ((ppa.channel * cfg.chips_per_channel) + ppa.chip) * cfg.dies_per_chip + ppa.die;
	return ppa;
}

MQSimOnlineModel::PPA MQSimOnlineModel::resolve_read_ppa(uint64_t lpn, bool *mapped) const
{
	std::unordered_map<uint64_t, PPA>::const_iterator it = mapping_table.find(lpn);

	if (it != mapping_table.end()) {
		if (mapped)
			*mapped = true;
		return it->second;
	}

	if (mapped)
		*mapped = false;
	return deterministic_ppa(lpn);
}

uint64_t MQSimOnlineModel::preload_write(uint64_t slba, uint32_t nlb)
{
	uint64_t sectors_per_page = std::max<uint64_t>(1, cfg.page_capacity_bytes / 512);
	uint64_t first_lpn = slba / sectors_per_page;
	uint64_t last_lpn = (slba + nlb - 1) / sectors_per_page;
	uint64_t allocated = 0;

	if (nlb == 0)
		return 0;

	for (uint64_t lpn = first_lpn; lpn <= last_lpn; lpn++) {
		if (mapping_table.find(lpn) != mapping_table.end())
			continue;
		mapping_table[lpn] = allocate_physical_page();
		allocated++;
	}

	preload_pages += allocated;
	return allocated;
}

uint64_t MQSimOnlineModel::estimate_latency_ns(const nvmev_mqsim_io_msg& req)
{
	uint64_t bytes_total = (uint64_t)req.nlb * 512;
	uint64_t sectors_per_page = std::max<uint64_t>(1, cfg.page_capacity_bytes / 512);
	uint64_t first_lpn = req.slba / sectors_per_page;
	uint64_t sector_offset = req.slba % sectors_per_page;
	uint64_t sectors_left = req.nlb;
	uint64_t media_ready_ns = req.submit_time_ns + cfg.controller_overhead_ns;
	uint64_t request_finish_ns = media_ready_ns;
	uint64_t original_lpn = first_lpn;
	uint64_t first_channel = 0;
	uint64_t first_chip = 0;
	uint64_t first_die = 0;
	uint64_t first_plane = 0;
	uint64_t first_lun = 0;
	uint64_t pages_in_request = 0;
	uint64_t mapped_pages_in_request = 0;
	uint64_t unmapped_pages_in_request = 0;
	bool captured_first_page = false;

	if (bytes_total == 0)
		return cfg.controller_overhead_ns;

	while (sectors_left > 0) {
		uint64_t sectors_this_page = std::min<uint64_t>(sectors_left, sectors_per_page - sector_offset);
		uint64_t bytes_this_page = sectors_this_page * 512;
		bool mapped = false;
		PPA ppa;
		uint64_t channel;
		uint64_t chip;
		uint64_t die;
		uint64_t plane;
		uint64_t lun;
		uint64_t start_ns;
		uint64_t finish_ns;

		if (req.opcode == NVMEV_MQSIM_IO_WRITE) {
			ppa = allocate_physical_page();
			mapping_table[first_lpn] = ppa;
			write_allocated_pages++;
			mapped = true;
		} else {
			ppa = resolve_read_ppa(first_lpn, &mapped);
		}

		channel = ppa.channel;
		chip = ppa.chip;
		die = ppa.die;
		plane = ppa.plane;
		lun = ppa.lun;
		start_ns = std::max(media_ready_ns, std::max(channel_next_available_ns[channel], lun_next_available_ns[lun]));
		finish_ns = start_ns + page_service_latency_ns(req.opcode, bytes_this_page);

		if (!captured_first_page) {
			first_channel = channel;
			first_chip = chip;
			first_die = die;
			first_plane = plane;
			first_lun = lun;
			captured_first_page = true;
		}

		channel_next_available_ns[channel] = finish_ns;
		lun_next_available_ns[lun] = finish_ns;
		if (req.opcode == NVMEV_MQSIM_IO_WRITE) {
			channel_write_ops[channel]++;
			lun_write_ops[lun]++;
		} else {
			channel_read_ops[channel]++;
			lun_read_ops[lun]++;
			if (mapped) {
				read_mapped_pages++;
				mapped_pages_in_request++;
			} else {
				read_unmapped_pages++;
				unmapped_pages_in_request++;
			}
		}
		request_finish_ns = std::max(request_finish_ns, finish_ns);
		pages_in_request++;

		sectors_left -= sectors_this_page;
		first_lpn++;
		sector_offset = 0;
	}

	total_requests++;
	total_pages += pages_in_request;
	{
		uint64_t latency_ns = request_finish_ns > req.submit_time_ns ? request_finish_ns - req.submit_time_ns : cfg.controller_overhead_ns;
		std::ostringstream trace;

		trace << "request_id=" << req.request_id
		      << " op=" << (req.opcode == NVMEV_MQSIM_IO_WRITE ? "write" : "read")
		      << " slba=" << req.slba
		      << " nlb=" << req.nlb
		      << " first_lpn=" << original_lpn
		      << " pages=" << pages_in_request
		      << " first_channel=" << first_channel
		      << " first_chip=" << first_chip
		      << " first_die=" << first_die
		      << " first_plane=" << first_plane
		      << " first_lun=" << first_lun
		      << " mapped_pages=" << mapped_pages_in_request
		      << " unmapped_pages=" << unmapped_pages_in_request
		      << " latency_ns=" << latency_ns;
		last_trace_line = trace.str();
	}

	return request_finish_ns > req.submit_time_ns ? request_finish_ns - req.submit_time_ns : cfg.controller_overhead_ns;
}

const std::string& MQSimOnlineModel::last_trace() const
{
	return last_trace_line;
}

void MQSimOnlineModel::print_stats(FILE *out) const
{
	uint64_t total_lun_ops = 0;
	uint64_t total_channel_ops = 0;

	for (size_t i = 0; i < lun_read_ops.size(); i++)
		total_lun_ops += lun_read_ops[i] + lun_write_ops[i];
	for (size_t i = 0; i < channel_read_ops.size(); i++)
		total_channel_ops += channel_read_ops[i] + channel_write_ops[i];

	fprintf(out, "MQSim online stats: requests=%" PRIu64 " nand_pages=%" PRIu64 "\n",
		total_requests, total_pages);
	fprintf(out, "  ftl: mapped_pages=%zu preload_pages=%" PRIu64 " write_allocated_pages=%" PRIu64 " read_mapped_pages=%" PRIu64 " read_unmapped_pages=%" PRIu64 "\n",
		mapping_table.size(), preload_pages, write_allocated_pages, read_mapped_pages, read_unmapped_pages);
	fprintf(out, "  channels:\n");
	for (size_t i = 0; i < channel_read_ops.size(); i++) {
		uint64_t ops = channel_read_ops[i] + channel_write_ops[i];
		double pct = total_channel_ops == 0 ? 0.0 : (100.0 * (double)ops / (double)total_channel_ops);

		if (channel_read_ops[i] == 0 && channel_write_ops[i] == 0)
			continue;
		fprintf(out, "    channel=%zu ops=%" PRIu64 " pct=%.2f reads=%" PRIu64 " writes=%" PRIu64 " next_available_ns=%" PRIu64 "\n",
			i, ops, pct, channel_read_ops[i], channel_write_ops[i], channel_next_available_ns[i]);
	}
	fprintf(out, "  luns:\n");
	for (size_t i = 0; i < lun_read_ops.size(); i++) {
		uint64_t ops = lun_read_ops[i] + lun_write_ops[i];
		double pct = total_lun_ops == 0 ? 0.0 : (100.0 * (double)ops / (double)total_lun_ops);

		if (lun_read_ops[i] == 0 && lun_write_ops[i] == 0)
			continue;
		fprintf(out, "    lun=%zu ops=%" PRIu64 " pct=%.2f reads=%" PRIu64 " writes=%" PRIu64 " next_available_ns=%" PRIu64 "\n",
			i, ops, pct, lun_read_ops[i], lun_write_ops[i], lun_next_available_ns[i]);
	}
}

void MQSimOnlineModel::print_compact_stats(FILE *out, size_t top_n) const
{
	struct Entry {
		size_t id;
		uint64_t ops;
		double pct;
	};
	std::vector<Entry> channels;
	std::vector<Entry> luns;
	uint64_t total_channel_ops = 0;
	uint64_t total_lun_ops = 0;
	double channel_min = std::numeric_limits<double>::max();
	double channel_max = 0.0;
	double lun_min = std::numeric_limits<double>::max();
	double lun_max = 0.0;

	for (size_t i = 0; i < channel_read_ops.size(); i++)
		total_channel_ops += channel_read_ops[i] + channel_write_ops[i];
	for (size_t i = 0; i < lun_read_ops.size(); i++)
		total_lun_ops += lun_read_ops[i] + lun_write_ops[i];

	for (size_t i = 0; i < channel_read_ops.size(); i++) {
		uint64_t ops = channel_read_ops[i] + channel_write_ops[i];
		double pct = total_channel_ops == 0 ? 0.0 : (100.0 * (double)ops / (double)total_channel_ops);

		channels.push_back({i, ops, pct});
		channel_min = std::min(channel_min, pct);
		channel_max = std::max(channel_max, pct);
	}

	for (size_t i = 0; i < lun_read_ops.size(); i++) {
		uint64_t ops = lun_read_ops[i] + lun_write_ops[i];
		double pct = total_lun_ops == 0 ? 0.0 : (100.0 * (double)ops / (double)total_lun_ops);

		luns.push_back({i, ops, pct});
		lun_min = std::min(lun_min, pct);
		lun_max = std::max(lun_max, pct);
	}

	std::sort(channels.begin(), channels.end(), [](const Entry& a, const Entry& b) {
		return a.ops > b.ops;
	});
	std::sort(luns.begin(), luns.end(), [](const Entry& a, const Entry& b) {
		return a.ops > b.ops;
	});

	fprintf(out, "MQSim online stats: requests=%" PRIu64 " nand_pages=%" PRIu64 "\n",
		total_requests, total_pages);
	fprintf(out, "  ftl: mapped_pages=%zu preload_pages=%" PRIu64 " write_allocated_pages=%" PRIu64 " read_mapped_pages=%" PRIu64 " read_unmapped_pages=%" PRIu64 "\n",
		mapping_table.size(), preload_pages, write_allocated_pages, read_mapped_pages, read_unmapped_pages);
	fprintf(out, "  channel distribution: ideal=%.2f%% min=%.2f%% max=%.2f%%\n",
		channels.empty() ? 0.0 : 100.0 / (double)channels.size(),
		channel_min == std::numeric_limits<double>::max() ? 0.0 : channel_min,
		channel_max);
	fprintf(out, "  lun distribution: ideal=%.2f%% min=%.2f%% max=%.2f%%\n",
		luns.empty() ? 0.0 : 100.0 / (double)luns.size(),
		lun_min == std::numeric_limits<double>::max() ? 0.0 : lun_min,
		lun_max);

	fprintf(out, "  hottest luns:");
	for (size_t i = 0; i < std::min(top_n, luns.size()); i++)
		fprintf(out, " lun%zu=%.2f%%(%" PRIu64 ")", luns[i].id, luns[i].pct, luns[i].ops);
	fprintf(out, "\n");

	fprintf(out, "  coldest luns:");
	for (size_t i = 0; i < std::min(top_n, luns.size()); i++) {
		const Entry& entry = luns[luns.size() - 1 - i];
		fprintf(out, " lun%zu=%.2f%%(%" PRIu64 ")", entry.id, entry.pct, entry.ops);
	}
	fprintf(out, "\n");
}
