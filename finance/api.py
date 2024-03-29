from __future__ import unicode_literals
import frappe, re
from frappe import _
from frappe.utils import get_fullname, get_datetime, now_datetime, get_url_to_form, date_diff, add_days,add_months, getdate
from frappe.contacts.doctype.address.address import get_address_display, get_default_address
from frappe.contacts.doctype.contact.contact import get_contact_details, get_default_contact
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt, now_datetime
from erpnext.accounts.utils import get_fiscal_year
from erpnext import get_company_currency
from erpnext.accounts.party import set_address_details as set_address_details_erpnext,set_contact_details,set_other_values,set_price_list,get_address_tax_category,set_taxes,get_payment_terms_template,get_due_date,get_party_account

@frappe.whitelist()
def leave_on_cancel(self,method):
	self.db_set("workflow_state","Cancelled")
	self.db_set("status","Cancelled")

@frappe.whitelist()
def install_on_submit(self, method):
	add_parts(self, method)
	
def add_parts(self, method):
	target_doc = frappe.get_doc("Serial No", self.items[0].serial_no.strip())
	target_doc.dongle_id = self.dongle_id
	target_doc.license_type = self.license_type
	target_doc.valid_up_to = self.valid_up_to
	for li in self.machine_parts: 
		target_doc.append("machine_parts", {
			"part_name": li.part_name,
			"no_of_parts" : li.no_of_parts,
			"serial_no" : li.serial_no,
			"original_serial" : li.serial_no
		})
	target_doc.save()
	frappe.db.commit()
	
@frappe.whitelist()
def get_party_details(party=None, party_type="Lead", ignore_permissions=False):

	if not party:
		return {}

	if not frappe.db.exists(party_type, party):
		frappe.throw(_("{0}: {1} does not exists").format(party_type, party))

	return _get_party_details(party, party_type, ignore_permissions)
	
	
def _get_party_details(party=None, party_type="Lead", ignore_permissions=False):

	out = frappe._dict({
		party_type.lower(): party
	})

	party = out[party_type.lower()]

	if not ignore_permissions and not frappe.has_permission(party_type, "read", party):
		frappe.throw(_("Not permitted for {0}").format(party), frappe.PermissionError)

	party = frappe.get_doc(party_type, party)
	
	set_organisation_details(out, party, party_type)
	set_address_details(out, party, party_type)
	set_contact_details(out, party, party_type)
	set_other_values(out, party, party_type)

	return out

def set_organisation_details(out, party, party_type):
	
	organisation = None
	
	if party_type == 'Lead':
		organisation = frappe.db.get_value("Lead", {"name": party.name}, "company_name")
	elif party_type == 'Customer':
		organisation = frappe.db.get_value("Customer", {"name": party.name}, "customer_name")
	elif party_type == 'Supplier':
		organisation = frappe.db.get_value("Supplier", {"name": party.name}, "supplier_name")

	out.update({'organisation': organisation})

def set_address_details(out, party, party_type):
	billing_address_field = "customer_address" if party_type == "Lead" \
		else party_type.lower() + "_address"
	out[billing_address_field] = get_default_address(party_type, party.name)
	
	# address display
	out.address_display = get_address_display(out[billing_address_field])

def set_contact_details(out, party, party_type):
	out.contact_person = get_default_contact(party_type, party.name)

	if not out.contact_person:
		out.update({
			"contact_person": None,
			"contact_display": None,
			"contact_email": None,
			"contact_mobile": None,
			"contact_phone": None,
			"contact_designation": None,
			"contact_department": None
		})
	else:
		out.update(get_contact_details(out.contact_person))

def set_other_values(out, party, party_type):
	# copy
	if party_type=="Customer":
		to_copy = ["customer_name", "customer_group", "territory", "language"]
	else:
		to_copy = ["supplier_name", "supplier_type", "language"]
	for f in to_copy:
		out[f] = party.get(f)
		
@frappe.whitelist()
def make_purchase_order_for_drop_shipment(source_name, for_supplier, target_doc=None):
	def set_missing_values(source, target):
		target.supplier = for_supplier
		target.apply_discount_on = ""
		target.additional_discount_percentage = 0.0
		target.discount_amount = 0.0

		default_price_list = frappe.get_value("Supplier", for_supplier, "default_price_list")
		if default_price_list:
			target.buying_price_list = default_price_list

		if any( item.delivered_by_supplier==1 for item in source.items):
			if source.shipping_address_name:
				target.shipping_address = source.shipping_address_name
				target.shipping_address_display = source.shipping_address
			else:
				target.shipping_address = source.customer_address
				target.shipping_address_display = source.address_display

			target.customer_contact_person = source.contact_person
			target.customer_contact_display = source.contact_display
			target.customer_contact_mobile = source.contact_mobile
			target.customer_contact_email = source.contact_email

		else:
			target.customer = ""
			target.customer_name = ""

		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_item(source, target, source_parent):
		target.schedule_date = source.delivery_date
		target.qty = flt(source.qty)
		target.stock_qty = (flt(source.qty) - flt(source.ordered_qty)) * flt(source.conversion_factor)

	doclist = get_mapped_doc("Sales Order", source_name, {
		"Sales Order": {
			"doctype": "Purchase Order",
			"field_no_map": [
				"address_display",
				"contact_display",
				"contact_mobile",
				"contact_email",
				"contact_person",
				"taxes_and_charges",
				"naming_series"
			],
			"validation": {
				"docstatus": ["=", 1]
			}
		},
		"Sales Order Item": {
			"doctype": "Purchase Order Item",
			"field_map":  [
				["name", "sales_order_item"],
				["parent", "sales_order"],
				["stock_uom", "stock_uom"],
				["uom", "uom"],
				["conversion_factor", "conversion_factor"],
				["delivery_date", "schedule_date"]
			],
			"field_no_map": [
				"rate",
				"price_list_rate"
			],
			"postprocess": update_item,
		}
	}, target_doc, set_missing_values)

	return doclist

@frappe.whitelist()
def make_meetings(source_name, doctype, ref_doctype, target_doc=None):

	def set_missing_values(source, target):
		target.party_type = doctype
		now = now_datetime()
		if ref_doctype == "Meeting Schedule":
			target.scheduled_from = target.scheduled_to = now
		else:
			target.meeting_from = target.meeting_to = now

	def update_contact(source, target, source_parent):
		if doctype == 'Lead':
			if not source.organization_lead:
				target.contact_person = source.lead_name

	doclist = get_mapped_doc(doctype, source_name, {
			doctype: {
				"doctype": ref_doctype,
				"field_map":  {
					'company_name': 'organisation',
					'name': 'party'
				},
				"field_no_map": [
					"naming_series"
				],
				"postprocess": update_contact
			}
		}, target_doc, set_missing_values)

	return doclist

@frappe.whitelist()
def get_items(customer):
	
	where_clause = ''
	where_clause += customer and " parent = '%s' " % customer.replace("'", "\'") or ''
	
	return frappe.db.sql("""
		SELECT 
			item_code
		FROM
			`tabCustomer Item`
		WHERE
			%s
		ORDER BY
			idx"""% where_clause, as_dict=1)


@frappe.whitelist()
def recalculate_depreciation(doc_name):
	doc = frappe.get_doc("Asset", doc_name)
	year_end = get_fiscal_year(doc.purchase_date)[2]
	useful_life_year_1 = date_diff(year_end,doc.purchase_date)
	
	if doc.schedules[0].depreciation_amount:
		sl_dep_year_1 = round((doc.schedules[1].depreciation_amount * useful_life_year_1)/ 365,2)
		sl_dep_year_last = round(doc.schedules[1].depreciation_amount - sl_dep_year_1,2)
		frappe.db.set_value("Asset", doc_name, "depreciation_method", "Manual")
		frappe.db.set_value("Depreciation Schedule", doc.schedules[0].name, "depreciation_amount", sl_dep_year_1)
		frappe.db.set_value("Depreciation Schedule", doc.schedules[0].name, "accumulated_depreciation_amount", sl_dep_year_1)
		total_depre = len(doc.get('schedules'))
		if (doc.total_number_of_depreciations >= len(doc.get('schedules'))):
			fields =dict(
				schedule_date = add_months(doc.next_depreciation_date, doc.total_number_of_depreciations*12),
				depreciation_amount = sl_dep_year_last,
				accumulated_depreciation_amount = doc.gross_purchase_amount - doc.expected_value_after_useful_life,
				parent = doc.name,
				parenttype = doc.doctype,
				parentfield = 'schedules',
				idx = len(doc.get('schedules'))+1
			)
			schedule = frappe.new_doc("Depreciation Schedule")
			schedule.db_set(fields, commit=True)
			schedule.insert(ignore_permissions=True)
			schedule.save(ignore_permissions=True)
			frappe.db.commit()
			doc.reload()
		else:
			frappe.db.set_value("Depreciation Schedule", doc.schedules[(len(doc.get('schedules')))-1].name, "depreciation_amount", sl_dep_year_last)
			frappe.db.commit()
		return sl_dep_year_1

@frappe.whitelist()
def employee_sales_person():
	sales_person_employee = frappe.db.sql("""
		SELECT employee
		FROM `tabSales Person`
		""")
	return sales_person_employee

@frappe.whitelist()
def docs_before_naming(self, method):
	from erpnext.accounts.utils import get_fiscal_year

	date = self.get("transaction_date") or self.get("posting_date") or getdate()

	fy = get_fiscal_year(date)[0]
	fiscal = frappe.db.get_value("Fiscal Year", fy, 'fiscal')

	if fiscal:
		self.fiscal = fiscal
	else:
		fy_years = fy.split("-")
		fiscal = fy_years[0][2:] + fy_years[1][2:]
		self.fiscal = fiscal

def si_before_validate(self,method):
	validate_document_name(self)


GST_INVOICE_NUMBER_FORMAT = re.compile(r"^[a-zA-Z0-9\-/]+$")   #alphanumeric and - /
def validate_document_name(doc, method=None):
	"""Validate GST invoice number requirements."""

	country = frappe.get_cached_value("Company", doc.company, "country")
	einvoice_enable = frappe.db.get_single_value("E Invoice Settings",'enable')
	# Date was chosen as start of next FY to avoid irritating current users.
	if country != "India" or getdate(doc.posting_date) < getdate("2021-04-01"):
		return

	if einvoice_enable and len(doc.name) > 16:
		frappe.throw(_("Maximum length of document number should be 16 characters as per GST rules. Please change the naming series."))

	if not GST_INVOICE_NUMBER_FORMAT.match(doc.name):
		frappe.throw(_("Document name should only contain alphanumeric values, dash(-) and slash(/) characters as per GST rules. Please change the naming series."))



@frappe.whitelist()
def get_party_details(party=None, account=None, party_type="Customer", company=None, posting_date=None,
	bill_date=None, price_list=None, currency=None, doctype=None, ignore_permissions=False, fetch_payment_terms_template=True,
	party_address=None, company_address=None, shipping_address=None, pos_profile=None):

	if not party:
		return {}
	if not frappe.db.exists(party_type, party):
		frappe.throw(_("{0}: {1} does not exists").format(party_type, party))
	return _get_party_details(party, account, party_type,
		company, posting_date, bill_date, price_list, currency, doctype, ignore_permissions,
		fetch_payment_terms_template, party_address, company_address, shipping_address, pos_profile)

def _get_party_details(party=None, account=None, party_type="Customer", company=None, posting_date=None,
	bill_date=None, price_list=None, currency=None, doctype=None, ignore_permissions=False,
	fetch_payment_terms_template=True, party_address=None, company_address=None, shipping_address=None, pos_profile=None):
	party_details = frappe._dict(set_account_and_due_date(party, account, party_type, company, posting_date, bill_date, doctype))
	party = party_details[party_type.lower()]

	if not ignore_permissions and not (frappe.has_permission(party_type, "read", party) or frappe.has_permission(party_type, "select", party)):
		frappe.throw(_("Not permitted for {0}").format(party), frappe.PermissionError)

	party = frappe.get_doc(party_type, party)
	currency = party.default_currency if party.get("default_currency") else get_company_currency(company)

	party_address, shipping_address = set_address_details_erpnext(party_details, party, party_type, doctype, company, party_address, company_address, shipping_address)
	set_contact_details(party_details, party, party_type)
	set_other_values(party_details, party, party_type)
	set_price_list(party_details, party, party_type, price_list, pos_profile)

	party_details["tax_category"] = get_address_tax_category(party.get("tax_category"),
		party_address, shipping_address if party_type != "Supplier" else party_address)

	if not party_details.get("taxes_and_charges"):
		party_details["taxes_and_charges"] = set_taxes(party.name, party_type, posting_date, company,
			customer_group=party_details.customer_group, supplier_group=party_details.supplier_group, tax_category=party_details.tax_category,
			billing_address=party_address, shipping_address=shipping_address)

	if cint(fetch_payment_terms_template):
		party_details["payment_terms_template"] = get_payment_terms_template(party.name, party_type, company)

	if not party_details.get("currency"):
		party_details["currency"] = currency

	# sales team
	if party_type=="Customer":
		party_details["sales_team"] = [{
			"sales_person": d.sales_person,
			"allocated_percentage": d.allocated_percentage or None
		} for d in party.get("sales_team")]

	# supplier tax withholding category
	if party_type == "Supplier" and party:
		party_details["supplier_tds"] = frappe.get_value(party_type, party.name, "tax_withholding_category")

	return party_details

def set_account_and_due_date(party, account, party_type, company, posting_date, bill_date, doctype):
	if doctype not in ["POS Invoice", "Sales Invoice", "Purchase Invoice"]:
		# not an invoice
		return {
			party_type.lower(): party
		}

	if party:
		account = get_party_account(party_type, party, company)

	account_fieldname = "debit_to" if party_type=="Customer" else "credit_to"
	out = {
		party_type.lower(): party,
		account_fieldname : account,
		"due_date": get_due_date(posting_date, party_type, party, company, bill_date)
	}

	return out

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_supplier(doctype, txt, searchfield, start, page_len, filters):
	supp_master_name = frappe.defaults.get_user_default("supp_master_name")
	if supp_master_name == "Supplier Name":
		fields = ["name", "supplier_group"]
	else:
		fields = ["name", "supplier_name", "supplier_group"]
	fields = ", ".join(fields)

	return frappe.db.sql("""select {field} from `tabSupplier`
		where docstatus < 2
			and ({key} like %(txt)s
				or supplier_name like %(txt)s)
			and name in (select supplier from `tabSales Order Item` where parent = %(parent)s)
			and name not in (select supplier from `tabPurchase Order` po inner join `tabPurchase Order Item` poi
			     on po.name=poi.parent where po.docstatus<2 and poi.sales_order=%(parent)s)
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			if(locate(%(_txt)s, supplier_name), locate(%(_txt)s, supplier_name), 99999),
			name, supplier_name
		limit %(start)s, %(page_len)s """.format(**{
			'field': fields,
			'key': frappe.db.escape(searchfield)
		}), {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len,
			'parent': filters.get('parent')
		})
