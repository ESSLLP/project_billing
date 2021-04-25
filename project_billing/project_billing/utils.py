import frappe
import json
from frappe import _
from frappe.utils import flt, today, add_days
from erpnext.stock.get_item_details import get_item_details
from erpnext import get_default_currency

# Task Hooks
def create_item_from_task(doc, method):
	'''
	before insert Task
	Create Item for billing for is_milestone
	'''
	if doc.project:
		project_template = frappe.db.get_value('Project', doc.project, 'project_template')
		if project_template:
			project_template_doc = frappe.get_doc("Project Template", project_template)

			if project_template_doc:
				template_task = [template for template in project_template_doc.tasks if template.get('subject') == doc.subject]
				if template_task and template_task[0]:
					if len(template_task) > 1:
						frappe.throw(_('Project Template contains multiple Tasks with same Subject <b>{}</b>')
							.format(template_task[0].get('subject')), title = _('Did not Create Tasks'))
					if template_task[0].get('is_milestone') == 1:
						doc.is_milestone = template_task[0].get('is_milestone')
						doc.billable_amount = template_task[0].get('billable_amount')

	if not doc.is_milestone:
		return

	if not frappe.db.exists('Item', doc.subject):
		if not doc.task_group:
			doc.task_group = 'All Item Groups'

		# Insert item
		item = frappe.get_doc({
			'doctype': 'Item',
			'item_code': doc.subject,
			'item_name': doc.subject,
			'item_group': doc.task_group,
			'description': doc.subject,
			'stock_uom': 'Percent',
			'is_stock_item': 0,
			'is_sales_item': 1,
			'is_service_item': 1,
			'is_purchase_item': 0,
			'show_in_website': 0,
			'is_pro_applicable': 0,
			'disabled': 0
		}).insert(ignore_permissions=True)
		item_name = item.name
	else:
		item_name = frappe.db.get_value('Item', doc.subject, 'name')

	# Link Item
	doc.item = item_name


def validate_task_billing_details(doc, method):
	'''
	validate Task
	Validate billable amounts with Project Estimate
	'''
	if flt(doc.progress) < flt(doc.percent_billed):
		frappe.throw(_('Task Progress cannot be less than the percentage already Invoiced'))

	if doc.is_milestone and flt(doc.billable_amount) <= 0:
		frappe.throw(_('Billable Amount is mandatory for Milestone Tasks'))

	if doc.is_milestone and not doc.project:
		frappe.throw(_('Project is mandatory for Milestone Tasks'))

	tasks = frappe.db.get_all('Task',
		filters={
			'project': doc.project,
			'is_milestone': 1,
			'name': ['!=', doc.name]
		},
		fields=[
			'sum(billable_amount) as total_billable_amount'
		])

	max_total_billable = flt(frappe.db.get_value('Project', doc.project, 'estimated_costing'))
	if max_total_billable <= 0:
		max_total_billable = flt(frappe.db.get_value('Project', doc.project, 'total_sales_amount'))

	total_tasks_billable = flt(doc.billable_amount) + flt(tasks[0].total_billable_amount) if tasks and tasks[0] else 0

	if max_total_billable > 0 and total_tasks_billable > max_total_billable:
		frappe.throw(_('Total billable amount ({}) for all Milestone Tasks exceeds the Estimated Cost / Total Sales Amount of Project ({})')
			.format(
				frappe.utils.fmt_money(abs(total_tasks_billable), currency=get_default_currency()),
				frappe.utils.fmt_money(abs(max_total_billable), currency=get_default_currency())
			),
			title=_('Did not Save')
		)


# Sales Invoice hooks
def validate_items_and_set_history(doc, method):
	'''
	validate Sales Invoice
	'''
	doc.invoice_retention_amount = 0
	if doc.project:
		for item in doc.items:
			if item.reference_task:
				if item.percent_billed != frappe.db.get_value('Task', item.reference_task, 'percent_billed'):
					frappe.throw(_('Progress of Tasks for billing is not up to date, please reselect Project to fetch billing items again'))

				# reset billing values based on invoice quantity
				retention_percentage = frappe.db.get_value('Project', doc.project, 'retention_percentage')
				actual_billable_amount = flt(item.billable_amount) - \
					(flt(item.billable_amount) * (flt(retention_percentage) / 100) if retention_percentage else 0)

				item.full_rate = (flt(item.billable_amount) / 100) * flt(doc.plc_conversion_rate) / flt(doc.conversion_rate)
				item.rate = (flt(actual_billable_amount) / 100) * flt(doc.plc_conversion_rate) / flt(doc.conversion_rate)
				item.full_amount = item.qty * item.full_rate
				item.amount =  item.qty * item.rate
				# retention amount without considering invoice currency
				item.retention_amount = (flt(item.billable_amount) / 100) * item.qty - \
					(flt(actual_billable_amount) / 100) *  item.qty

				# aggregate and set total retention amount)
				doc.invoice_retention_amount += item.retention_amount

	# set billing history
	set_billing_history(doc)


def update_project_and_task(doc, method):
	'''
	on submit Sales Invoice
	on cancel Sales Invoice
	Update Task billed percentage
	Update Project invoiced retention amount
	'''
	if doc.project:
		# Update Task billed percentage
		for item in doc.items:
			if item.reference_task and frappe.db.exists('Task', item.reference_task):
				billed_percentage = frappe.db.get_value('Task', item.reference_task, 'percent_billed')
				if doc.docstatus == 1:
					billed_percentage = billed_percentage + item.qty
				else:
					billed_percentage =  billed_percentage - item.qty
					# Also set percent_billed, so that the invoice can be amended
					# TODO: verify
					item.percent_billed = billed_percentage
				frappe.db.set_value('Task', item.reference_task, 'percent_billed', billed_percentage)

		# Update Project Retention Amount
		if doc.invoice_retention_amount and doc.invoice_retention_amount > 0:
			total_retention_amount = frappe.db.get_value('Project', doc.project, 'total_retention_amount')
			if doc.docstatus == 1:
				total_retention_amount = total_retention_amount + doc.invoice_retention_amount
			else:
				total_retention_amount = total_retention_amount - doc.invoice_retention_amount

			frappe.db.set_value('Project', doc.project, 'total_retention_amount', total_retention_amount)


def set_billing_history(doc):
	'''
	set billing history html only if the Sales Invoice is a 'project progressive invoice'
	'''
	if doc.project and any(bool(item.get('reference_task')) for item in doc.items):
		past_invoices = frappe.db.get_all('Sales Invoice',
			filters = {
				'project': doc.project,
				'name': ['!=', doc.name],
				'docstatus': 1
				},
			fields=['name', 'posting_date', 'status', 'outstanding_amount']
		)

		item_details = frappe.db.get_all('Sales Invoice Item',
			filters = {
				'parent': ['in', [invoice_name.get('name') for invoice_name in past_invoices]],
				'reference_task': ['!=', '']
				},
			fields = ['parent', 'item_code', 'reference_task', 'qty', 'amount', 'billable_amount']
		)

		for item_detail in item_details:
			invoice_status = [inv for inv in past_invoices if inv['name'] == item_detail['parent']]
			item_detail.update(invoice_status[0])
		print(item_details)
		doc.project_billing_history = frappe.render_template('templates/includes/billing_history.html',
			dict(items=item_details, currency=doc.currency))
	else:
		doc.project_billing_history = ''


@frappe.whitelist()
def get_billing_details(doc):
	'''
	get billable task details for the project
	invoked from Sales Invoice, on project
	'''
	invoice = json.loads(doc)
	if not invoice.get('project'):
		return

	project_details = frappe.db.get_value('Project', invoice.get('project'),
		['percent_complete_method', 'retention_percentage', 'sales_order', 'total_sales_amount'],
		as_dict=1
	)

	if project_details.percent_complete_method == 'Task Completion':
		filters = {'status': 'Completed'}
	elif project_details.percent_complete_method in ['Task Progress', 'Task Weight']:
		filters = {'progress': ['<=', 100]}
	else:
		return

	filters.update({'project': invoice.get('project'), 'is_milestone': 1, 'percent_billed': ['<', 100]})

	tasks = frappe.db.get_all('Task', filters=filters, fields=['name', 'item', 'task_group', 'description',
		'uom', 'billable_amount', 'status', 'progress', 'percent_billed', 'task_weight'])

	if not tasks:
		frappe.throw(_('No Tasks for the selected Project indicate billable progress, please try after updating Task progress / status'))

	items = []
	invoice_retention_amount = 0.0

	for task in tasks:
		if project_details.percent_complete_method != 'Task Completion' and task.progress <= task.percent_billed:
			continue

		item_details = get_item_details({
			'doctype': 'Sales Invoice',
			'item_code': task.item,
			'company': invoice.get('company'),
			'selling_price_list': invoice.get('price_list'),
			'price_list_currency': invoice.get('currency'),
			'plc_conversion_rate': invoice.get('plc_conversion_rate'),
			'conversion_rate': invoice.get('conversion_rate')
		})

		if project_details.percent_complete_method == 'Task Progress':
			item_details['qty'] = task.progress - task.percent_billed
		elif project_details.percent_complete_method == 'Task Completion':
			item_details['qty'] = 100 - task.percent_billed if task.percent_billed < 100 else 100
		elif project_details.percent_complete_method == 'Task Weight':
			item_details['qty'] = task.progress - task.percent_billed # TODO: same as Task Progress for now, fix!

		# actual billable amount based on retention percentage
		actual_billable_amount = flt(task.billable_amount) - \
			(flt(task.billable_amount) * (flt(project_details.retention_percentage) / 100) if project_details.retention_percentage else 0)

		item_details['billable_amount'] = flt(task.billable_amount)
		item_details['full_rate'] = (flt(task.billable_amount) / 100) * \
			flt(invoice.get('plc_conversion_rate')) / flt(invoice.get('conversion_rate'))
		item_details['rate'] = (flt(actual_billable_amount) / 100) * \
			flt(invoice.get('plc_conversion_rate')) / flt(invoice.get('conversion_rate'))
		item_details['full_amount'] = item_details['qty'] * item_details['full_rate']
		item_details['amount'] =  item_details['qty'] * item_details['rate']
		item_details['uom'] = task.uom
		item_details['reference_task'] = task.name
		item_details['task_progress'] = task.progress
		item_details['percent_billed'] = task.percent_billed
		item_details['sales_order'] = project_details.sales_order
		item_details['retention_amount'] = (flt(task.billable_amount) / 100) * item_details['qty'] - \
			(flt(actual_billable_amount) / 100) * item_details['qty']
		items.append(item_details)

		# aggregate total retention amount for the invoice
		invoice_retention_amount += item_details['retention_amount']

	return items, invoice_retention_amount
