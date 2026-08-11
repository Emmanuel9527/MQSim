#include "mqsim_native_online_model.h"

#include <algorithm>
#include <fstream>
#include <inttypes.h>
#include <sstream>
#include <stdexcept>
#include <string.h>

#include "src/exec/Execution_Parameter_Set.h"
#include "src/exec/IO_Flow_Parameter_Set.h"
#include "src/exec/SSD_Device.h"
#include "src/sim/Engine.h"
#include "src/ssd/Host_Interface_NVMe.h"
#include "src/ssd/NVM_PHY_ONFI.h"
#include "src/ssd/NVM_Transaction_Flash.h"
#include "src/ssd/SSD_Defs.h"
#include "src/utils/rapidxml/rapidxml.hpp"

MQSimNativeOnlineModel *MQSimNativeOnlineModel::active_instance = NULL;

MQSimNativeOnlineModel::MQSimNativeOnlineModel()
	: exec_params(NULL), online_flow(NULL), ssd(NULL), host_interface(NULL),
	  sectors_per_page(1), total_requests(0), preload_requests(0),
	  preload_sectors(0), read_requests(0), write_requests(0),
	  completed_requests(0), flash_read_transactions(0),
	  flash_write_transactions(0), flash_erase_transactions(0),
	  channel_count(0), chips_per_channel(0), dies_per_chip(0),
	  events_run(0), max_pending(0)
{
}

MQSimNativeOnlineModel::~MQSimNativeOnlineModel()
{
	clear_pending();
	delete ssd;
	delete online_flow;
	delete exec_params;
	if (active_instance == this)
		active_instance = NULL;
}

bool MQSimNativeOnlineModel::load_config(const std::string& path, std::string *error)
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

		if (!root) {
			if (error)
				*error = "SSD config does not contain Execution_Parameter_Set";
			return false;
		}
		exec_params->XML_deserialize(root);
	} catch (const std::exception& ex) {
		if (error)
			*error = std::string("failed to parse SSD config: ") + ex.what();
		return false;
	}
	return true;
}

void MQSimNativeOnlineModel::create_single_online_flow()
{
	Device_Parameter_Set& device = exec_params->SSD_Device_Configuration;
	IO_Flow_Parameter_Set_Synthetic *flow = new IO_Flow_Parameter_Set_Synthetic;

	flow->Device_Level_Data_Caching_Mode = SSD_Components::Caching_Mode::TURNED_OFF;
	flow->Type = Flow_Type::SYNTHETIC;
	flow->Priority_Class = IO_Flow_Priority_Class::HIGH;
	flow->Channel_No = device.Flash_Channel_Count;
	flow->Channel_IDs = new flash_channel_ID_type[flow->Channel_No];
	for (int i = 0; i < flow->Channel_No; i++)
		flow->Channel_IDs[i] = (flash_channel_ID_type)i;
	flow->Chip_No = device.Chip_No_Per_Channel;
	flow->Chip_IDs = new flash_chip_ID_type[flow->Chip_No];
	for (int i = 0; i < flow->Chip_No; i++)
		flow->Chip_IDs[i] = (flash_chip_ID_type)i;
	flow->Die_No = device.Flash_Parameters.Die_No_Per_Chip;
	flow->Die_IDs = new flash_die_ID_type[flow->Die_No];
	for (int i = 0; i < flow->Die_No; i++)
		flow->Die_IDs[i] = (flash_die_ID_type)i;
	flow->Plane_No = device.Flash_Parameters.Plane_No_Per_Die;
	flow->Plane_IDs = new flash_plane_ID_type[flow->Plane_No];
	for (int i = 0; i < flow->Plane_No; i++)
		flow->Plane_IDs[i] = (flash_plane_ID_type)i;
	flow->Initial_Occupancy_Percentage = 0;
	flow->Working_Set_Percentage = 100;
	flow->Synthetic_Generator_Type = Utils::Request_Generator_Type::QUEUE_DEPTH;
	flow->Read_Percentage = 100;
	flow->Address_Distribution = Utils::Address_Distribution_Type::RANDOM_UNIFORM;
	flow->Percentage_of_Hot_Region = 0;
	flow->Generated_Aligned_Addresses = true;
	flow->Address_Alignment_Unit = std::max(1U, device.Flash_Parameters.Page_Capacity / SECTOR_SIZE_IN_BYTE);
	flow->Request_Size_Distribution = Utils::Request_Size_Distribution_Type::FIXED;
	flow->Average_Request_Size = flow->Address_Alignment_Unit;
	flow->Variance_Request_Size = 0;
	flow->Seed = device.Seed;
	flow->Average_No_of_Reqs_in_Queue = 1;
	flow->Bandwidth = 0;
	flow->Stop_Time = 0;
	flow->Total_Requests_To_Generate = 0;

	online_flow = flow;
	exec_params->Host_Configuration.IO_Flow_Definitions.clear();
	exec_params->Host_Configuration.IO_Flow_Definitions.push_back(online_flow);
}

bool MQSimNativeOnlineModel::initialize(const std::string& ssd_config_path, std::string *error)
{
	try {
		Simulator->Reset();
		exec_params = new Execution_Parameter_Set;
		if (!load_config(ssd_config_path, error))
			return false;

		exec_params->SSD_Device_Configuration.HostInterface_Type = HostInterface_Types::NVME;
		exec_params->SSD_Device_Configuration.Enabled_Preconditioning = false;
		exec_params->SSD_Device_Configuration.Caching_Mechanism = SSD_Components::Caching_Mechanism::SIMPLE;
		sectors_per_page = std::max(1U, exec_params->SSD_Device_Configuration.Flash_Parameters.Page_Capacity / SECTOR_SIZE_IN_BYTE);
		channel_count = exec_params->SSD_Device_Configuration.Flash_Channel_Count;
		chips_per_channel = exec_params->SSD_Device_Configuration.Chip_No_Per_Channel;
		dies_per_chip = exec_params->SSD_Device_Configuration.Flash_Parameters.Die_No_Per_Chip;
		channel_read_ops.assign(channel_count, 0);
		channel_write_ops.assign(channel_count, 0);
		channel_erase_ops.assign(channel_count, 0);
		lun_read_ops.assign((size_t)channel_count * chips_per_channel * dies_per_chip, 0);
		lun_write_ops.assign((size_t)channel_count * chips_per_channel * dies_per_chip, 0);
		lun_erase_ops.assign((size_t)channel_count * chips_per_channel * dies_per_chip, 0);

		create_single_online_flow();
		ssd = new SSD_Device(&exec_params->SSD_Device_Configuration,
				     &exec_params->Host_Configuration.IO_Flow_Definitions);
		host_interface = (SSD_Components::Host_Interface_NVMe *)ssd->Host_interface;
		active_instance = this;
		((SSD_Components::NVM_PHY_ONFI *)ssd->PHY)->ConnectToTransactionServicedSignal(flash_transaction_trampoline);
		host_interface->Create_new_stream(IO_Flow_Priority_Class::HIGH, 0,
						  host_interface->Get_max_logical_sector_address(),
						  0, 0);
		host_interface->Enable_online_mode(completion_trampoline);
		start_online_engine();
	} catch (const std::exception& ex) {
		if (error)
			*error = std::string("native MQSim online init failed: ") + ex.what();
		return false;
	}
	return true;
}

void MQSimNativeOnlineModel::start_online_engine()
{
	Simulator->Prepare_simulation();
}

uint64_t MQSimNativeOnlineModel::preload_write(uint64_t slba, uint32_t nlb)
{
	uint64_t loaded_sectors = 0;
	uint64_t current_slba = slba;
	uint32_t sectors_left = nlb;

	while (sectors_left > 0) {
			uint32_t sectors = std::min<uint32_t>(sectors_left, sectors_per_page);
			nvmev_mqsim_io_msg req;

			memset(&req, 0, sizeof(req));
			req.magic = NVMEV_MQSIM_MAGIC;
			req.version = NVMEV_MQSIM_VERSION;
			req.type = NVMEV_MQSIM_MSG_IO_REQUEST;
			req.opcode = NVMEV_MQSIM_IO_WRITE;
			req.submit_time_ns = Simulator->Time();
			req.slba = current_slba;
			req.nlb = sectors;
			submit_and_drain(req);
			current_slba += sectors;
			sectors_left -= sectors;
			loaded_sectors += sectors;
		preload_requests++;
	}
	preload_sectors += loaded_sectors;
	return (loaded_sectors + sectors_per_page - 1) / sectors_per_page;
}

uint64_t MQSimNativeOnlineModel::estimate_latency_ns(const nvmev_mqsim_io_msg& req)
{
	return submit_and_drain(req);
}

bool MQSimNativeOnlineModel::submit_request(const nvmev_mqsim_io_msg& req)
{
	SSD_Components::UserRequestType type =
		req.opcode == NVMEV_MQSIM_IO_WRITE ? SSD_Components::UserRequestType::WRITE :
						 SSD_Components::UserRequestType::READ;
	PendingRequest *pending = new PendingRequest;
	SSD_Components::User_Request *request;

	pending->external_id = req.request_id;
	pending->submit_time_ns = req.submit_time_ns;
	pending->original = req;

	Simulator->Fast_forward((sim_time_type)req.submit_time_ns);
	request = host_interface->Submit_online_request(type, 0, req.slba, req.nlb,
							(sim_time_type)req.submit_time_ns);
	pending_by_request[request] = pending;
	total_requests++;
	if (req.opcode == NVMEV_MQSIM_IO_WRITE)
		write_requests++;
	else
		read_requests++;
	if (pending_by_request.size() > max_pending)
		max_pending = pending_by_request.size();
	return true;
}

bool MQSimNativeOnlineModel::run_until_next_completion()
{
	size_t completed_before = completed_replies.size();

	while (completed_replies.size() == completed_before && !pending_by_request.empty()) {
		if (!Simulator->Run_next_event())
			return false;
		events_run++;
	}
	return completed_replies.size() > completed_before;
}

bool MQSimNativeOnlineModel::pop_completed_reply(nvmev_mqsim_io_msg *reply, std::string *trace)
{
	if (completed_replies.empty())
		return false;
	if (reply)
		*reply = completed_replies.front();
	if (trace)
		*trace = completed_traces.empty() ? std::string() : completed_traces.front();
	completed_replies.pop_front();
	if (!completed_traces.empty())
		completed_traces.pop_front();
	return true;
}

bool MQSimNativeOnlineModel::has_pending_requests() const
{
	return !pending_by_request.empty();
}

size_t MQSimNativeOnlineModel::pending_count() const
{
	return pending_by_request.size();
}

uint64_t MQSimNativeOnlineModel::submit_and_drain(const nvmev_mqsim_io_msg& req)
{
	nvmev_mqsim_io_msg reply;

	submit_request(req);
	while (!pop_completed_reply(&reply)) {
		if (!run_until_next_completion())
			throw std::runtime_error("MQSim event queue drained before online request completed");
	}

	return reply.latency_ns;
}

void MQSimNativeOnlineModel::handle_completion(SSD_Components::User_Request *request,
					       sim_time_type completion_time)
{
	std::unordered_map<SSD_Components::User_Request *, PendingRequest *>::iterator it =
		pending_by_request.find(request);

	if (it == pending_by_request.end())
		return;
	PendingRequest *pending = it->second;
	nvmev_mqsim_io_msg reply = pending->original;
	uint64_t latency = completion_time > pending->submit_time_ns ?
		(uint64_t)completion_time - pending->submit_time_ns : 0;
	std::ostringstream trace;

	reply.type = NVMEV_MQSIM_MSG_IO_REPLY;
	reply.latency_ns = latency;
	reply.status = 0;
	trace << "request_id=" << pending->external_id
	      << " op=" << (pending->original.opcode == NVMEV_MQSIM_IO_WRITE ? "write" : "read")
	      << " slba=" << pending->original.slba
	      << " nlb=" << pending->original.nlb
	      << " lpn=" << (pending->original.slba / sectors_per_page)
	      << " sectors_per_page=" << sectors_per_page
	      << " completion_ns=" << completion_time
	      << " latency_ns=" << latency
	      << " pending=" << pending_by_request.size() - 1;
	last_trace_line = trace.str();
	completed_replies.push_back(reply);
	completed_traces.push_back(last_trace_line);
	completed_requests++;
	pending_by_request.erase(it);
	delete pending;
}

void MQSimNativeOnlineModel::completion_trampoline(SSD_Components::User_Request *request,
						   sim_time_type completion_time)
{
	if (active_instance)
		active_instance->handle_completion(request, completion_time);
}

std::string MQSimNativeOnlineModel::describe() const
{
	std::ostringstream out;

	out << "native-event-driven"
	    << " sectors/page=" << sectors_per_page
	    << " channels=" << channel_count
	    << " chips/channel=" << chips_per_channel
	    << " dies/chip=" << dies_per_chip
	    << " preload_requests=" << preload_requests
	    << " preload_sectors=" << preload_sectors;
	return out.str();
}

const std::string& MQSimNativeOnlineModel::last_trace() const
{
	return last_trace_line;
}

void MQSimNativeOnlineModel::print_stats(FILE *out) const
{
	uint64_t total_channel_ops = 0;
	uint64_t total_lun_ops = 0;

	for (size_t i = 0; i < channel_read_ops.size(); i++)
		total_channel_ops += channel_read_ops[i] + channel_write_ops[i] + channel_erase_ops[i];
	for (size_t i = 0; i < lun_read_ops.size(); i++)
		total_lun_ops += lun_read_ops[i] + lun_write_ops[i] + lun_erase_ops[i];

	fprintf(out, "MQSim native online stats: requests=%" PRIu64 " completed=%" PRIu64 "\n",
		total_requests, completed_requests);
	fprintf(out, "  preload: write_requests=%" PRIu64 " sectors=%" PRIu64 "\n",
		preload_requests, preload_sectors);
	fprintf(out, "  online: reads=%" PRIu64 " writes=%" PRIu64 " pending=%zu\n",
		read_requests, write_requests, pending_by_request.size());
	fprintf(out, "  async: completed_queue=%zu max_pending=%" PRIu64 " events_run=%" PRIu64 "\n",
		completed_replies.size(), max_pending, events_run);
	fprintf(out, "  flash transactions: reads=%" PRIu64 " writes=%" PRIu64 " erases=%" PRIu64 "\n",
		flash_read_transactions, flash_write_transactions, flash_erase_transactions);
	fprintf(out, "  channels:\n");
	for (size_t i = 0; i < channel_read_ops.size(); i++) {
		uint64_t ops = channel_read_ops[i] + channel_write_ops[i] + channel_erase_ops[i];
		double pct = total_channel_ops == 0 ? 0.0 : 100.0 * (double)ops / (double)total_channel_ops;

		if (ops == 0)
			continue;
		fprintf(out, "    channel=%zu ops=%" PRIu64 " pct=%.2f reads=%" PRIu64 " writes=%" PRIu64 " erases=%" PRIu64 "\n",
			i, ops, pct, channel_read_ops[i], channel_write_ops[i], channel_erase_ops[i]);
	}
	fprintf(out, "  luns:\n");
	for (size_t i = 0; i < lun_read_ops.size(); i++) {
		uint64_t ops = lun_read_ops[i] + lun_write_ops[i] + lun_erase_ops[i];
		double pct = total_lun_ops == 0 ? 0.0 : 100.0 * (double)ops / (double)total_lun_ops;

		if (ops == 0)
			continue;
		fprintf(out, "    lun=%zu ops=%" PRIu64 " pct=%.2f reads=%" PRIu64 " writes=%" PRIu64 " erases=%" PRIu64 "\n",
			i, ops, pct, lun_read_ops[i], lun_write_ops[i], lun_erase_ops[i]);
	}
}

void MQSimNativeOnlineModel::print_compact_stats(FILE *out, size_t top_n) const
{
	struct Entry {
		size_t id;
		uint64_t ops;
		double pct;
		uint64_t reads;
		uint64_t writes;
		uint64_t erases;
	};
	std::vector<Entry> channels;
	std::vector<Entry> luns;
	std::vector<Entry> read_channels;
	std::vector<Entry> read_luns;
	uint64_t total_channel_ops = 0;
	uint64_t total_lun_ops = 0;
	uint64_t total_channel_reads = 0;
	uint64_t total_lun_reads = 0;
	double channel_min = 0.0, channel_max = 0.0;
	double lun_min = 0.0, lun_max = 0.0;
	double read_channel_min = 0.0, read_channel_max = 0.0;
	double read_lun_min = 0.0, read_lun_max = 0.0;

	for (size_t i = 0; i < channel_read_ops.size(); i++) {
		total_channel_ops += channel_read_ops[i] + channel_write_ops[i] + channel_erase_ops[i];
		total_channel_reads += channel_read_ops[i];
	}
	for (size_t i = 0; i < lun_read_ops.size(); i++) {
		total_lun_ops += lun_read_ops[i] + lun_write_ops[i] + lun_erase_ops[i];
		total_lun_reads += lun_read_ops[i];
	}

	for (size_t i = 0; i < channel_read_ops.size(); i++) {
		uint64_t ops = channel_read_ops[i] + channel_write_ops[i] + channel_erase_ops[i];
		double pct = total_channel_ops == 0 ? 0.0 : 100.0 * (double)ops / (double)total_channel_ops;
		double read_pct = total_channel_reads == 0 ? 0.0 : 100.0 * (double)channel_read_ops[i] / (double)total_channel_reads;

		channels.push_back({i, ops, pct, channel_read_ops[i], channel_write_ops[i], channel_erase_ops[i]});
		read_channels.push_back({i, channel_read_ops[i], read_pct, channel_read_ops[i], 0, 0});
		if (i == 0 || pct < channel_min)
			channel_min = pct;
		if (i == 0 || pct > channel_max)
			channel_max = pct;
		if (i == 0 || read_pct < read_channel_min)
			read_channel_min = read_pct;
		if (i == 0 || read_pct > read_channel_max)
			read_channel_max = read_pct;
	}
	for (size_t i = 0; i < lun_read_ops.size(); i++) {
		uint64_t ops = lun_read_ops[i] + lun_write_ops[i] + lun_erase_ops[i];
		double pct = total_lun_ops == 0 ? 0.0 : 100.0 * (double)ops / (double)total_lun_ops;
		double read_pct = total_lun_reads == 0 ? 0.0 : 100.0 * (double)lun_read_ops[i] / (double)total_lun_reads;

		luns.push_back({i, ops, pct, lun_read_ops[i], lun_write_ops[i], lun_erase_ops[i]});
		read_luns.push_back({i, lun_read_ops[i], read_pct, lun_read_ops[i], 0, 0});
		if (i == 0 || pct < lun_min)
			lun_min = pct;
		if (i == 0 || pct > lun_max)
			lun_max = pct;
		if (i == 0 || read_pct < read_lun_min)
			read_lun_min = read_pct;
		if (i == 0 || read_pct > read_lun_max)
			read_lun_max = read_pct;
	}
	std::sort(channels.begin(), channels.end(), [](const Entry& a, const Entry& b) {
		return a.ops > b.ops;
	});
	std::sort(luns.begin(), luns.end(), [](const Entry& a, const Entry& b) {
		return a.ops > b.ops;
	});
	std::sort(read_channels.begin(), read_channels.end(), [](const Entry& a, const Entry& b) {
		return a.ops > b.ops;
	});
	std::sort(read_luns.begin(), read_luns.end(), [](const Entry& a, const Entry& b) {
		return a.ops > b.ops;
	});

	fprintf(out, "MQSim native online stats: requests=%" PRIu64 " completed=%" PRIu64 "\n",
		total_requests, completed_requests);
	fprintf(out, "  preload: write_requests=%" PRIu64 " sectors=%" PRIu64 "\n",
		preload_requests, preload_sectors);
	fprintf(out, "  online: reads=%" PRIu64 " writes=%" PRIu64 " pending=%zu\n",
		read_requests, write_requests, pending_by_request.size());
	fprintf(out, "  async: completed_queue=%zu max_pending=%" PRIu64 " events_run=%" PRIu64 "\n",
		completed_replies.size(), max_pending, events_run);
	fprintf(out, "  flash transactions: reads=%" PRIu64 " writes=%" PRIu64 " erases=%" PRIu64 "\n",
		flash_read_transactions, flash_write_transactions, flash_erase_transactions);
	fprintf(out, "  channel distribution: ideal=%.2f%% min=%.2f%% max=%.2f%% total_ops=%" PRIu64 "\n",
		channels.empty() ? 0.0 : 100.0 / (double)channels.size(),
		channel_min, channel_max, total_channel_ops);
	fprintf(out, "  lun distribution: ideal=%.2f%% min=%.2f%% max=%.2f%% total_ops=%" PRIu64 "\n",
		luns.empty() ? 0.0 : 100.0 / (double)luns.size(),
		lun_min, lun_max, total_lun_ops);
	fprintf(out, "  read channel distribution: ideal=%.2f%% min=%.2f%% max=%.2f%% total_reads=%" PRIu64 "\n",
		read_channels.empty() ? 0.0 : 100.0 / (double)read_channels.size(),
		read_channel_min, read_channel_max, total_channel_reads);
	fprintf(out, "  read lun distribution: ideal=%.2f%% min=%.2f%% max=%.2f%% total_reads=%" PRIu64 "\n",
		read_luns.empty() ? 0.0 : 100.0 / (double)read_luns.size(),
		read_lun_min, read_lun_max, total_lun_reads);
	fprintf(out, "  hottest luns:");
	for (size_t i = 0; i < std::min(top_n, luns.size()); i++)
		fprintf(out, " lun%zu=%.2f%%(r=%" PRIu64 ",w=%" PRIu64 ",e=%" PRIu64 ")",
			luns[i].id, luns[i].pct, luns[i].reads, luns[i].writes, luns[i].erases);
	fprintf(out, "\n");
	fprintf(out, "  coldest luns:");
	for (size_t i = 0; i < std::min(top_n, luns.size()); i++) {
		const Entry& entry = luns[luns.size() - 1 - i];
		fprintf(out, " lun%zu=%.2f%%(r=%" PRIu64 ",w=%" PRIu64 ",e=%" PRIu64 ")",
			entry.id, entry.pct, entry.reads, entry.writes, entry.erases);
	}
	fprintf(out, "\n");
	fprintf(out, "  hottest read luns:");
	for (size_t i = 0; i < std::min(top_n, read_luns.size()); i++)
		fprintf(out, " lun%zu=%.2f%%(reads=%" PRIu64 ")",
			read_luns[i].id, read_luns[i].pct, read_luns[i].reads);
	fprintf(out, "\n");
}

void MQSimNativeOnlineModel::handle_flash_transaction(SSD_Components::NVM_Transaction_Flash *transaction)
{
	uint32_t channel = transaction->Address.ChannelID;
	uint32_t chip = transaction->Address.ChipID;
	uint32_t die = transaction->Address.DieID;
	uint64_t lun = lun_index(channel, chip, die);

	if (channel >= channel_read_ops.size() || lun >= lun_read_ops.size())
		return;

	if (transaction->Type == SSD_Components::Transaction_Type::READ) {
		flash_read_transactions++;
		channel_read_ops[channel]++;
		lun_read_ops[lun]++;
	} else if (transaction->Type == SSD_Components::Transaction_Type::WRITE) {
		flash_write_transactions++;
		channel_write_ops[channel]++;
		lun_write_ops[lun]++;
	} else if (transaction->Type == SSD_Components::Transaction_Type::ERASE) {
		flash_erase_transactions++;
		channel_erase_ops[channel]++;
		lun_erase_ops[lun]++;
	}
}

void MQSimNativeOnlineModel::flash_transaction_trampoline(SSD_Components::NVM_Transaction_Flash *transaction)
{
	if (active_instance)
		active_instance->handle_flash_transaction(transaction);
}

uint64_t MQSimNativeOnlineModel::lun_index(uint32_t channel, uint32_t chip, uint32_t die) const
{
	return ((uint64_t)channel * chips_per_channel + chip) * dies_per_chip + die;
}

void MQSimNativeOnlineModel::clear_pending()
{
	for (std::unordered_map<SSD_Components::User_Request *, PendingRequest *>::iterator it = pending_by_request.begin();
	     it != pending_by_request.end(); it++)
		delete it->second;
	pending_by_request.clear();
	completed_replies.clear();
	completed_traces.clear();
}
